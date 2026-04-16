#pragma once

#include "../../cbp-ng/cbp.hpp"
#include "../../cbp-ng/harcom.hpp"

using namespace hcm;

struct openevolve_predictor : predictor {
    /*
     * Poorly/inefficiently predict one instruction per cycle using an SRAM
     * array of simple two-bit counters indexed by a hashed PC.
     *
     * This is adapted directly from cbp-ng's tutorial_04 example predictor,
     * while keeping the workflow's expected predictor name.
     */

    // 4096-entry 2-bit counter table (12-bit index) – modest capacity boost with limited storage cost
    ram<val<2>, 4096> counters;
    reg<2>  counter;
    // 12-bit global history register: captures a slightly longer path history
    reg<12> ghr;

    /* Compact 12-bit gshare index generator with light-weight XOR folding */
    val<12> compute_index(val<64> pc) {
        /* Fold the 64-bit PC into 12 bits via XOR of 12-bit slices */
        val<12> pc_hash = pc.make_array(val<12>{}).fold_xor();
        /* Combine with current global history */
        val<12> ghr_bits = val<12>{ghr};
        return pc_hash ^ ghr_bits;
    }

    val<1> predict1([[maybe_unused]] val<64> inst_pc) override
    {
        // Lightweight gshare predictor: XOR-folded PC with global history
        auto index = compute_index(inst_pc);

        // Read counter and keep for later update
        counter = counters.read(index);

        // Predict branch taken if counter MSB is 1
        return counter >> 1;
    };

    val<1> predict2([[maybe_unused]] val<64> inst_pc) override
    {
        // Re-use the same prediction for the second-level predictor.
        return counter >> 1;
    }

    inline val<2> update_counter(val<2> current, val<1> taken) {
        val<2> increased = select(current == 3, current, val<2>{current + 1});
        val<2> decreased = select(current == 0, current, val<2>{current - 1});
        return select(taken, increased, decreased);
    }

    void update_condbr([[maybe_unused]] val<64> branch_pc,
                       [[maybe_unused]] val<1> taken,
                       [[maybe_unused]] val<64> next_pc) override
    {
        val<2> newcounter = update_counter(counter, taken);
        val<1> performing_update = val<1>{newcounter != counter};

        // Memory write to counter table may cost an extra cycle
        need_extra_cycle(performing_update);

        execute_if(performing_update, [&](){
            // Recompute same gshare index used during prediction
            auto index = compute_index(branch_pc);
            counters.write(index, newcounter);
        });

        // Update global history register (shift left, insert newest outcome)
        ghr = (ghr << 1) | taken;
    }

    void update_cycle([[maybe_unused]] instruction_info &block_end_info) override
    {
    }

    // These will never be called because this predictor never calls
    // reuse_prediction().
    val<1> reuse_predict1([[maybe_unused]] val<64> inst_pc) override
    {
        return hard<0>{};
    };

    val<1> reuse_predict2([[maybe_unused]] val<64> inst_pc) override
    {
        return hard<0>{};
    }
};
