#!/usr/bin/env python3
"""Convert a CSL2002 ChampSim L2 dump to a canonical drmemtrace file."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

MAGIC = b"CSL2002\n"
L2_RECORD = struct.Struct("<QQQQIBBBB")
TRACE_ENTRY = struct.Struct("<HHQ")

# ChampSim access_type.
CS_LOAD, CS_RFO, CS_PREFETCH, CS_WRITE, CS_TRANSLATION = range(5)

# DynamoRIO trace_type_t values.
TRACE_READ = 0
TRACE_WRITE = 1
TRACE_PREFETCH = 2
TRACE_INSTR = 10
TRACE_THREAD = 22
TRACE_THREAD_EXIT = 23
TRACE_PID = 24
TRACE_HEADER = 25
TRACE_FOOTER = 26
TRACE_MARKER = 28
TRACE_INSTR_NO_FETCH = 29

# DynamoRIO trace_marker_type_t values.
MARKER_TIMESTAMP = 2
MARKER_CPU_ID = 3
MARKER_FILETYPE = 9
MARKER_CACHE_LINE_SIZE = 10
MARKER_VERSION = 12
MARKER_PAGE_SIZE = 18

TRACE_VERSION = 7
FILETYPE_FILTERED_X86_64 = 0x01 | 0x40 | 0x100


@dataclass(frozen=True)
class L2Access:
    cycle: int
    paddr: int
    vaddr: int
    ip: int
    cpu: int
    access_type: int
    is_instr: bool
    from_l2_prefetcher: bool
    is_warmup: bool


@dataclass
class Counts:
    warmup_l2_accesses: int = 0
    simulation_l2_accesses: int = 0
    warmup_refs: int = 0
    sim_refs: int = 0
    skipped_l2_generated_prefetches: int = 0


def entry(record_type: int, size: int, value: int) -> bytes:
    return TRACE_ENTRY.pack(record_type, size, value & 0xFFFFFFFFFFFFFFFF)


def marker(marker_type: int, value: int) -> bytes:
    return entry(TRACE_MARKER, marker_type, value)


def read_accesses(path: Path) -> Iterator[L2Access]:
    with path.open("rb") as source:
        if source.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"{path} is not a CSL2002 L2 dump")

        while raw := source.read(L2_RECORD.size):
            if len(raw) != L2_RECORD.size:
                raise ValueError(f"{path} ends with a partial L2 record")
            cycle, paddr, vaddr, ip, cpu, kind, is_instr, from_l2_pf, is_warmup = L2_RECORD.unpack(raw)
            yield L2Access(
                cycle,
                paddr,
                vaddr,
                ip,
                cpu,
                kind,
                bool(is_instr),
                bool(from_l2_pf),
                bool(is_warmup),
            )


def write_header(output: BinaryIO, tid: int, pid: int, line_size: int, page_size: int) -> None:
    output.write(entry(TRACE_HEADER, 0, TRACE_VERSION))
    output.write(marker(MARKER_VERSION, TRACE_VERSION))
    output.write(marker(MARKER_FILETYPE, FILETYPE_FILTERED_X86_64))
    output.write(entry(TRACE_THREAD, 0, tid))
    output.write(entry(TRACE_PID, 0, pid))
    output.write(marker(MARKER_CACHE_LINE_SIZE, line_size))
    output.write(marker(MARKER_PAGE_SIZE, page_size))
    output.write(marker(MARKER_TIMESTAMP, 1))
    output.write(marker(MARKER_CPU_ID, 0))


def drmemtrace_type(access: L2Access) -> int | None:
    if access.access_type in (CS_LOAD, CS_TRANSLATION):
        return TRACE_READ
    if access.access_type in (CS_RFO, CS_WRITE):
        return TRACE_WRITE
    if access.access_type == CS_PREFETCH:
        # TRACE_TYPE_HARDWARE_PREFETCH is rejected by some DynamoRIO builds.
        return TRACE_PREFETCH
    return None


def convert(
    source: Path,
    destination: Path,
    *,
    line_size: int,
    page_size: int,
    use_virtual: bool,
    include_l2_prefetches: bool,
) -> Counts:
    counts = Counts()
    destination.parent.mkdir(parents=True, exist_ok=True)

    opener = gzip.open if destination.suffix == ".gz" else open
    with opener(destination, "wb") as output:
        write_header(output, tid=1, pid=1, line_size=line_size, page_size=page_size)
        last_timestamp = 0

        for access in read_accesses(source):
            if access.from_l2_prefetcher and not include_l2_prefetches:
                counts.skipped_l2_generated_prefetches += 1
                continue

            address = access.vaddr if use_virtual and access.vaddr else access.paddr
            if not address:
                continue

            if access.cycle - last_timestamp >= 100_000:
                output.write(marker(MARKER_TIMESTAMP, max(access.cycle, 1)))
                output.write(marker(MARKER_CPU_ID, access.cpu))
                last_timestamp = access.cycle

            if access.is_instr:
                # This is a real instruction fetch that reached ChampSim L2.
                output.write(entry(TRACE_INSTR, 1, address))
                emitted_refs = 1
            else:
                kind = drmemtrace_type(access)
                if kind is None:
                    continue
                # Supply the issuing PC without introducing a synthetic I-cache access.
                output.write(entry(TRACE_INSTR_NO_FETCH, 1, access.ip))
                output.write(entry(kind, line_size, address))
                emitted_refs = 2

            if access.is_warmup:
                counts.warmup_l2_accesses += 1
                counts.warmup_refs += emitted_refs
            else:
                counts.simulation_l2_accesses += 1
                counts.sim_refs += emitted_refs

        output.write(entry(TRACE_THREAD_EXIT, 0, 1))
        output.write(entry(TRACE_FOOTER, 0, 0))

    return counts


def make_indir(trace: Path, root: Path) -> Path:
    directory = root / "drmemtrace.champsim_l2.1.0.dir"
    trace_dir = directory / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)
    target = trace_dir / "drmemtrace.champsim_l2.1.trace.gz"
    if target.exists():
        target.unlink()
    try:
        os.link(trace, target)
    except OSError:
        target.write_bytes(trace.read_bytes())
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--line-size", type=int, default=64)
    parser.add_argument("--page-size", type=int, default=4096)
    parser.add_argument("--virtual", action="store_true", help="Prefer virtual addresses (physical is the default)")
    parser.add_argument("--include-l2-prefetches", action="store_true")
    parser.add_argument("--counts-json", type=Path)
    parser.add_argument("--indir-root", type=Path)
    args = parser.parse_args()

    counts = convert(
        args.source,
        args.output,
        line_size=args.line_size,
        page_size=args.page_size,
        use_virtual=args.virtual,
        include_l2_prefetches=args.include_l2_prefetches,
    )

    counts_path = args.counts_json or Path(f"{args.output}.counts.json")
    payload = asdict(counts)
    payload["drrun_args"] = f"-warmup_refs {counts.warmup_refs} -sim_refs {counts.sim_refs}"
    counts_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {counts.warmup_l2_accesses + counts.simulation_l2_accesses} L2 accesses to {args.output}")
    print(f"drcachesim: -warmup_refs {counts.warmup_refs} -sim_refs {counts.sim_refs}")
    print(f"Counts: {counts_path}")
    if args.indir_root:
        print(f"Input directory: {make_indir(args.output, args.indir_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
