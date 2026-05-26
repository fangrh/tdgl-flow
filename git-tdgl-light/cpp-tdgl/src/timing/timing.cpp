#include "timing/timing.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <stdexcept>

using json = nlohmann::json;

TimingSchedule parse_timing_json(const std::string& json_path) {
    std::ifstream f(json_path);
    if (!f.is_open())
        throw std::runtime_error("Cannot open timing file: " + json_path);
    json j;
    f >> j;

    TimingSchedule sched;
    sched.solve_time = j.at("solve_time").get<double>();
    sched.n_steps = j.at("n_steps").get<int>();

    for (auto& s : j.at("steps")) {
        TimingStep step;
        step.je_start = s.at("je_start").get<double>();
        step.je_end = s.at("je_end").get<double>();
        step.ramp_start = s.at("ramp_start").get<double>();
        step.ramp_end = s.at("ramp_end").get<double>();
        step.stable_end = s.at("stable_end").get<double>();
        sched.steps.push_back(step);
    }

    if (j.contains("ramp_down_steps")) {
        for (auto& s : j.at("ramp_down_steps")) {
            TimingStep step;
            step.je_start = s.at("je_start").get<double>();
            step.je_end = s.at("je_end").get<double>();
            step.ramp_start = s.at("ramp_start").get<double>();
            step.ramp_end = s.at("ramp_end").get<double>();
            step.stable_end = s.at("stable_end").get<double>();
            sched.steps.push_back(step);
        }
    }

    return sched;
}
