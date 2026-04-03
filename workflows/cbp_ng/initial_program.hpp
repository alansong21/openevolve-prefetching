#pragma once

#include "../../cbp-ng/cbp.hpp"
#include "../../cbp-ng/harcom.hpp"

using namespace hcm;

#include "../../cbp-ng/predictors/common.hpp"

struct openevolve_predictor : predictor {
    /*
     * Tiny 1 K-entry bimodal predictor (2-bit counters).
     * Only ~2 KB of state, single-cycle access, no block reuse.
     * Provides a much better baseline than the trivial always-NT policy.
     */

    /* ---- configuration ---- */
    static constexpr u64 LOG_TAB   = 10;                // 1 K entries
    static constexpr u64 TAB_SIZE  = 1ull << LOG_TAB;
    static constexpr u64 INDEX_MASK = TAB_SIZE - 1;

    /* ---- storage ---- */
    ram<val<2>, TAB_SIZE> table;   // 2-bit saturating counters
    reg<2>                ctr;     // counter read this prediction
    reg<LOG_TAB>          idx;     // index used this prediction

    /* ---- helper: saturating counter update ---- */
    static inline val<2> next_ctr(val<2> old, val<1> taken)
    {
        return update_ctr(old, taken);  // utility from common.hpp
    }

    /* ---- prediction interface ---- */
    val<1> predict1(val<64> inst_pc) override
    {
        /* simple hash: PC[11:2] (byte aligned) */
        idx = (inst_pc >> 2) & hard<INDEX_MASK>{};
        ctr = table.read(idx);

        /* we issue only one prediction per cycle */
        reuse_prediction(hard<0>{});

        /* MSB of 2-bit counter is direction */
        return ctr >> 1;
    }

    /* no reuse path – just return last result */
    val<1> reuse_predict1([[maybe_unused]] val<64>) override { return ctr >> 1; }

    /* second-level identical to first-level */
    val<1> predict2([[maybe_unused]] val<64>) override { return ctr >> 1; }
    val<1> reuse_predict2([[maybe_unused]] val<64>) override { return ctr >> 1; }

    /* ---- update on each conditional branch ---- */
    void update_condbr(val<64> branch_pc,
                       val<1>  taken,
                       [[maybe_unused]] val<64> next_pc) override
    {
        val<LOG_TAB> uidx = (branch_pc >> 2) & hard<INDEX_MASK>{};
        val<2>       old  = table.read(uidx);
        val<2>       upd  = next_ctr(old, taken);

        /* write back only if value changed */
        val<1> modify = (upd != old);
        need_extra_cycle(modify);           // array write costs a cycle
        execute_if(modify, [&](){ table.write(uidx, upd); });
    }

    /* nothing to do at block end */
    void update_cycle([[maybe_unused]] instruction_info&) override {}
};
