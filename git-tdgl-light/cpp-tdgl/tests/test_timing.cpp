#include "timing/timing.h"
#include <cassert>
#include <cmath>
#include <fstream>
#include <iostream>

void test_parse_simple() {
    const char* path = "/tmp/test_timing_simple.json";
    {
        std::ofstream f(path);
        f << R"({
            "mode": "simple",
            "n_steps": 3,
            "solve_time": 45.0,
            "steps": [
                {"je_start":0.0, "je_end":0.5, "ramp_start":0.0,  "ramp_end":5.0,  "stable_end":15.0},
                {"je_start":0.5, "je_end":1.0, "ramp_start":15.0, "ramp_end":20.0, "stable_end":30.0},
                {"je_start":1.0, "je_end":1.5, "ramp_start":30.0, "ramp_end":35.0, "stable_end":45.0}
            ]
        })";
    }
    auto sched = parse_timing_json(path);
    assert(sched.n_steps == 3);
    assert(std::abs(sched.solve_time - 45.0) < 1e-12);
    assert(sched.steps.size() == 3);
    assert(std::abs(sched.steps[0].je_end - 0.5) < 1e-12);
    assert(std::abs(sched.steps[2].stable_end - 45.0) < 1e-12);
    std::cout << "test_parse_simple PASSED\n";
}

void test_parse_with_ramp_down() {
    const char* path = "/tmp/test_timing_rampdown.json";
    {
        std::ofstream f(path);
        f << R"({
            "mode": "simple",
            "n_steps": 1,
            "solve_time": 30.0,
            "steps": [
                {"je_start":0.0, "je_end":1.0, "ramp_start":0.0, "ramp_end":5.0, "stable_end":15.0}
            ],
            "ramp_down_steps": [
                {"je_start":1.0, "je_end":0.0, "ramp_start":15.0, "ramp_end":20.0, "stable_end":30.0}
            ]
        })";
    }
    auto sched = parse_timing_json(path);
    assert(sched.steps.size() == 2);
    assert(std::abs(sched.steps[1].je_end - 0.0) < 1e-12);
    std::cout << "test_parse_with_ramp_down PASSED\n";
}

int main() {
    test_parse_simple();
    test_parse_with_ramp_down();
    std::cout << "All timing tests passed.\n";
    return 0;
}
