#pragma once
#include <string>
#include <vector>

struct TimingStep {
    double je_start = 0.0;
    double je_end = 0.0;
    double ramp_start = 0.0;
    double ramp_end = 0.0;
    double stable_end = 0.0;
};

struct TimingSchedule {
    std::vector<TimingStep> steps;
    double solve_time = 0.0;
    int n_steps = 0;
};

TimingSchedule parse_timing_json(const std::string& json_path);
