#pragma once

#include <string>

struct Options {
    double solve_time = 0.0;
    double skip_time = 0.0;
    double dt_init = 1e-4;
    double dt_max = 1e-1;
    bool adaptive = true;
    int adaptive_window = 10;
    int max_solve_retries = 10;
    double adaptive_time_step_multiplier = 0.25;
    double terminal_psi = 0.0;
    int save_every = 100;
    bool include_screening = false;
    int max_iterations_per_step = 1000;
    double screening_tolerance = 1e-3;
    double screening_step_size = 0.1;
    double screening_step_drag = 0.5;
    std::string field_units = "mT";
    std::string current_units = "uA";
    double applied_field = 0.0;
};
