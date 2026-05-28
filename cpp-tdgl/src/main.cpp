#include "device/device.h"
#include "mesh/io.h"
#include "solver/solver.h"
#include "solution/solution.h"
#include "timing/timing.h"
#include "sync/minio_synchronizer.h"
#include <nlohmann/json.hpp>
#include <highfive/H5File.hpp>
#include <iostream>
#include <fstream>
#include <string>
#include <signal.h>
#include <execinfo.h>
#include <unistd.h>
#include <unordered_map>
#include <cmath>
#include <limits>
#include <memory>

static double unit_to_si(const std::string& unit) {
    static const std::unordered_map<std::string, double> table = {
        {"A", 1.0}, {"mA", 1e-3}, {"uA", 1e-6}, {"nA", 1e-9},
        {"m", 1.0}, {"mm", 1e-3}, {"um", 1e-6}, {"nm", 1e-9},
        {"T", 1.0}, {"mT", 1e-3}, {"uT", 1e-6},
    };
    auto it = table.find(unit);
    if (it != table.end()) return it->second;
    throw std::runtime_error("Unknown unit: " + unit);
}

static void crash_handler(int sig) {
    void *array[50];
    int size = backtrace(array, 50);
    fprintf(stderr, "Signal %d:\n", sig);
    backtrace_symbols_fd(array, size, STDERR_FILENO);
    _exit(1);
}

int main(int argc, char* argv[]) {
    signal(SIGSEGV, crash_handler);
    signal(SIGABRT, crash_handler);
    std::ios_base::sync_with_stdio(true);
    std::string mesh_path = "data/reference.h5";
    std::string output_path = "data/cpp_output.h5";
    std::string output_dir;
    double source_current = 1.0;
    double drain_current = -1.0;
    double applied_field_override = std::numeric_limits<double>::quiet_NaN();
    std::string log_file;
    std::string restart_path;
    std::string timing_path;
    std::string solver_options_json;
    bool enable_sync = false;
    std::string sync_url = "http://minio:9000";
    std::string sync_bucket = "tdgl-results";
    std::string sync_prefix = "";

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--mesh" && i + 1 < argc) mesh_path = argv[++i];
        else if (arg == "--output" && i + 1 < argc) output_path = argv[++i];
        else if (arg == "--output-dir" && i + 1 < argc) output_dir = argv[++i];
        else if (arg == "--source-current" && i + 1 < argc)
            source_current = std::stod(argv[++i]);
        else if (arg == "--drain-current" && i + 1 < argc)
            drain_current = std::stod(argv[++i]);
        else if (arg == "--applied-field" && i + 1 < argc)
            applied_field_override = std::stod(argv[++i]);
        else if (arg == "--log-file" && i + 1 < argc)
            log_file = argv[++i];
        else if (arg == "--restart" && i + 1 < argc)
            restart_path = argv[++i];
        else if (arg == "--timing" && i + 1 < argc)
            timing_path = argv[++i];
        else if (arg == "--solver-options" && i + 1 < argc)
            solver_options_json = argv[++i];
        else if (arg == "--enable-sync")
            enable_sync = true;
        else if (arg == "--sync-url" && i + 1 < argc)
            sync_url = argv[++i];
        else if (arg == "--sync-bucket" && i + 1 < argc)
            sync_bucket = argv[++i];
        else if (arg == "--sync-prefix" && i + 1 < argc)
            sync_prefix = argv[++i];
        else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: tdgl_solve --mesh <input.h5> --output <output.h5>"
                      << " [--source-current <A>] [--drain-current <A>]"
                      << " [--applied-field <Bz>]"
                      << " [--timing <timing.json>]"
                      << " [--solver-options <json>]"
                      << " [--restart <result.h5>]"
                      << " [--output-dir <dir>]"
                      << " [--log-file <path>]"
                      << " [--enable-sync]"
                      << " [--sync-url <url>] [--sync-bucket <bucket>] [--sync-prefix <prefix>]\n";
            return 0;
        }
    }

    // Redirect stdout to log file if specified
    std::ofstream log_stream;
    std::streambuf* old_cout_buf = nullptr;
    if (!log_file.empty()) {
        log_stream.open(log_file, std::ios::out | std::ios::trunc);
        if (log_stream.is_open()) {
            old_cout_buf = std::cout.rdbuf();
            std::cout.rdbuf(log_stream.rdbuf());
            std::cout << std::unitbuf;  // flush after each write for real-time reading
        }
    }

    try {
        std::cout << "Reading input: " << mesh_path << "\n";
        auto device = read_device(mesh_path);
        auto options = read_options(mesh_path);

        std::cout << "Device: " << device.name
                  << ", sites=" << device.mesh.num_sites
                  << ", edges=" << device.mesh.edge_mesh->num_edges
                  << ", terminals=" << device.terminals.size() << "\n";

        std::cout << "Options: solve_time=" << options.solve_time
                  << ", dt_init=" << options.dt_init
                  << ", dt_max=" << options.dt_max
                  << ", adaptive=" << options.adaptive
                  << ", save_every=" << options.save_every << "\n";

        // Read epsilon from mesh HDF5 if present
        Eigen::VectorXd epsilon_values;
        {
            HighFive::File mesh_file(mesh_path, HighFive::File::ReadOnly);
            if (mesh_file.exist("epsilon")) {
                auto ds = mesh_file.getDataSet("epsilon");
                auto dims = ds.getDimensions();
                size_t n = dims[0];
                epsilon_values.resize(n);
                ds.read_raw(epsilon_values.data());
                std::cout << "Epsilon: " << n << " values loaded\n";
            }
        }

        // Apply solver options from JSON string
        if (!solver_options_json.empty()) {
            auto opts = nlohmann::json::parse(solver_options_json);
            if (opts.contains("dt_init")) options.dt_init = opts["dt_init"].get<double>();
            if (opts.contains("dt_max")) options.dt_max = opts["dt_max"].get<double>();
            if (opts.contains("adaptive")) options.adaptive = opts["adaptive"].get<bool>();
            if (opts.contains("save_every")) options.save_every = opts["save_every"].get<int>();
            std::cout << "Solver options from JSON: dt_init=" << options.dt_init
                      << " dt_max=" << options.dt_max << " save_every=" << options.save_every << "\n";
        }

        int ne = device.mesh.edge_mesh->num_edges;
        Eigen::MatrixX2d applied_A = Eigen::MatrixX2d::Zero(ne, 2);

        // Applied magnetic field (uniform Bz) -> vector potential in Landau gauge
        double applied_Bz = 0.0;
        if (!std::isnan(applied_field_override))
            applied_Bz = applied_field_override;
        else if (options.applied_field != 0.0)
            applied_Bz = options.applied_field;

        if (applied_Bz != 0.0 && device.A0 > 0) {
            // Convert applied field from field_units to Tesla
            double fu = unit_to_si(options.field_units);
            double Bz_SI = applied_Bz * fu;

            // Convert edge centers to meters and center at device centroid
            double lu = unit_to_si(device.length_units);
            double xi = device.layer.coherence_length;
            auto centers = device.mesh.edge_mesh->centers;
            Eigen::ArrayXd cx = (xi * centers.col(0).array()) * lu;
            Eigen::ArrayXd cy = (xi * centers.col(1).array()) * lu;
            cx -= (cx.minCoeff() + cx.maxCoeff()) / 2.0;
            cy -= (cy.minCoeff() + cy.maxCoeff()) / 2.0;

            // Landau gauge: A = (-Bz*y/2, Bz*x/2), then scale by 1/A0
            for (int i = 0; i < ne; ++i) {
                applied_A(i, 0) = -Bz_SI * cy(i) / (2.0 * device.A0);
                applied_A(i, 1) =  Bz_SI * cx(i) / (2.0 * device.A0);
            }
            std::cout << "Applied field: " << applied_Bz << " " << options.field_units
                      << " (Bz_SI=" << Bz_SI << " T, A0=" << device.A0 << ")\n";
        }

        std::unique_ptr<TdglSolver> solver_ptr;
        std::unique_ptr<SplitSolutionWriter> split_writer;
        std::unique_ptr<MinioSynchronizer> syncer;

        if (enable_sync && !output_dir.empty()) {
            syncer = std::make_unique<MinioSynchronizer>(sync_url, sync_bucket, sync_prefix);
            syncer->start();
        }

        if (!output_dir.empty() && !timing_path.empty()) {
            // Use SplitSolutionWriter for per-step HDF5 output
            split_writer = std::make_unique<SplitSolutionWriter>(output_dir, device.mesh, device);
            split_writer->write_mesh();

            auto timing = parse_timing_json(timing_path);
            options.solve_time = timing.solve_time;
            std::cout << "Timing: " << timing.n_steps << " steps, solve_time=" << timing.solve_time << "\n";

            solver_ptr = std::make_unique<TdglSolver>(
                device, options, applied_A, timing,
                1.0, "", restart_path);

            // Capture timing, split_writer, and syncer in callback
            auto timing_copy = timing;
            solver_ptr->on_step_complete = [&, split_writer_ptr = split_writer.get(), syncer_ptr = syncer.get()](int step_idx) {
                split_writer_ptr->end_step();
                if (syncer_ptr) {
                    char buf[256];
                    snprintf(buf, sizeof(buf), "%s/step_%04d.h5", output_dir.c_str(), step_idx);
                    syncer_ptr->upload_step(buf, step_idx);
                    syncer_ptr->upload_index(output_dir + "/discrete_index.json");
                }
                int next_step = step_idx + 1;
                if (next_step < (int)timing_copy.steps.size()) {
                    double next_ramp_start = timing_copy.steps[next_step].ramp_start;
                    double je = solver_ptr->terminal_current_at(next_ramp_start);
                    split_writer_ptr->begin_step(next_step, je,
                        timing_copy.steps[next_step].ramp_start,
                        timing_copy.steps[next_step].stable_end);
                }
            };

            // Begin first step
            double je0 = solver_ptr->terminal_current_at(timing.steps[0].ramp_start);
            split_writer->begin_step(0, je0,
                timing.steps[0].ramp_start,
                timing.steps[0].stable_end);

        } else if (!timing_path.empty()) {
            auto timing = parse_timing_json(timing_path);
            options.solve_time = timing.solve_time;
            std::cout << "Timing: " << timing.n_steps << " steps, solve_time=" << timing.solve_time << "\n";
            solver_ptr = std::make_unique<TdglSolver>(
                device, options, applied_A, timing,
                1.0, output_path, restart_path);
        } else {
            std::map<std::string, double> terminal_currents;
            for (auto& t : device.terminals) {
                if (t.name == "source") terminal_currents["source"] = source_current;
                if (t.name == "drain") terminal_currents["drain"] = drain_current;
            }
            std::cout << "Terminal currents: source=" << source_current
                      << ", drain=" << drain_current << "\n";
            solver_ptr = std::make_unique<TdglSolver>(
                device, options, applied_A, terminal_currents,
                1.0, output_path, restart_path);
        }
        std::cout << "Running solver...\n";
        if (options.save_every <= 0) options.save_every = 1;
        solver_ptr->solve();

        if (split_writer) {
            split_writer->write_manifest("run_" + std::to_string(time(nullptr)), options.solve_time);
        }

        if (syncer) {
            syncer->upload_mesh(output_dir + "/mesh.h5");
            syncer->upload_manifest(output_dir + "/manifest.json");
            syncer->stop();
        }

        std::cout << "Done.\n";
        if (old_cout_buf) std::cout.rdbuf(old_cout_buf);
        return 0;

    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << "\n";
        if (old_cout_buf) std::cout.rdbuf(old_cout_buf);
        return 1;
    }
}
