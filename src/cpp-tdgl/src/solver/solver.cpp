#include "solver/solver.h"
#include "solver/adaptive_step.h"
#include "solver/observables.h"
#include "solver/screening.h"
#include <highfive/H5File.hpp>
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <unordered_map>
#include <vector>
#include <deque>

using Eigen::VectorXd;
using Eigen::VectorXcd;
using Eigen::MatrixX2d;

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

TdglSolver::TdglSolver(const Device& device, const Options& options,
                       const MatrixX2d& applied_vector_potential,
                       const std::map<std::string, double>& terminal_currents,
                       double disorder_epsilon,
                       const std::string& output_path,
                       const std::string& restart_path)
    : device_(device), options_(options), applied_A_(applied_vector_potential),
      terminal_currents_(terminal_currents), time_(0.0) {

    u_ = device_.layer.u;
    gamma_ = device_.layer.gamma;
    int ns = device_.mesh.num_sites;
    int ne = device_.mesh.edge_mesh->num_edges;
    int nb = device_.mesh.edge_mesh->boundary_edge_indices.size();

    epsilon_ = VectorXd::Constant(ns, disorder_epsilon);

    // Fixed sites: terminal site indices (for psi Dirichlet BC)
    Eigen::VectorXi fixed_sites(0);
    if (options_.terminal_psi == 0.0 || !std::isnan(options_.terminal_psi)) {
        int total_fixed = 0;
        for (auto& t : device_.terminals) {
            total_fixed += t.site_indices.size();
        }
        fixed_sites.resize(total_fixed);
        int idx = 0;
        for (auto& t : device_.terminals) {
            for (int i = 0; i < t.site_indices.size(); ++i) {
                fixed_sites(idx++) = t.site_indices(i);
            }
        }
    }

    // Build operators
    operators_.build_operators(device_.mesh, fixed_sites, fixed_sites.size() > 0);

    // Pin row 0 and column 0 of mu_laplacian to make the singular Neumann
    // system non-singular (ensures consistent GPU/CPU solutions).
    for (Eigen::SparseMatrix<double>::InnerIterator it(operators_.mu_laplacian_, 0); it; ++it) {
        it.valueRef() = (it.col() == 0) ? 1.0 : 0.0;
    }
    for (int i = 1; i < ns; ++i) {
        for (Eigen::SparseMatrix<double>::InnerIterator it(operators_.mu_laplacian_, i); it; ++it) {
            if (it.col() == 0) it.valueRef() = 0.0;
        }
    }

    // Factorize mu_laplacian
    poisson_solver_.factorize(operators_.mu_laplacian_);

    // Initialize psi
    psi_ = VectorXcd::Ones(ns);
    if (fixed_sites.size() > 0) {
        for (int i = 0; i < fixed_sites.size(); ++i) {
            psi_(fixed_sites(i)) = std::complex<double>(options_.terminal_psi, 0);
        }
    }

    mu_ = VectorXd::Zero(ns);
    supercurrent_ = VectorXd::Zero(ne);
    normal_current_ = VectorXd::Zero(ne);

    // Restart from previous solution if restart_path is given
    if (!restart_path.empty()) {
        HighFive::File file(restart_path, HighFive::File::ReadOnly);
        auto data_group = file.getGroup("data");
        auto keys = data_group.listObjectNames();

        // Find the last step
        int last_step = -1;
        for (auto& k : keys) {
            try { last_step = std::max(last_step, std::stoi(k)); }
            catch (...) {}
        }

        if (last_step >= 0) {
            std::string step_key = std::to_string(last_step);
            auto step_group = data_group.getGroup(step_key);

            std::vector<double> psi_r = step_group.getDataSet("psi_real").read<std::vector<double>>();
            std::vector<double> psi_i = step_group.getDataSet("psi_imag").read<std::vector<double>>();
            std::vector<double> mu_data = step_group.getDataSet("mu").read<std::vector<double>>();

            for (int i = 0; i < ns; ++i) {
                psi_(i) = std::complex<double>(psi_r[i], psi_i[i]);
                mu_(i) = mu_data[i];
            }

            time_ = step_group.getAttribute("time").read<double>();
            // Reset time so the solver runs for a fresh solve_time period
            double prev_time = time_;
            time_ = 0.0;
            std::cout << "Restarted from step " << last_step
                      << ", prev_time=" << prev_time << " (reset to 0)\n";
        }
    }

    // mu_boundary: current density at boundary edges from terminals
    mu_boundary_ = VectorXd::Zero(nb);

    // Compute J_scale: converts physical terminal currents to dimensionless
    double j_scale = 1.0;
    if (device_.K0 > 0) {
        double current_si = unit_to_si(options_.current_units);
        double length_si = unit_to_si(device_.length_units);
        j_scale = 4.0 * (current_si / length_si) / device_.K0;
    }

    update_mu_boundary(j_scale);

    // Set link exponents from applied vector potential
    operators_.set_link_exponents(applied_A_);

    // Screening initialization
    include_screening_ = options_.include_screening;
    if (include_screening_) {
        double xi = device_.layer.coherence_length;
        double Lambda = device_.Lambda;
        double pi = M_PI;
        double A_scale = 1.0 / (pi * Lambda) * 1e-6;
        screening_areas_ = A_scale * device_.mesh.areas.array() * (xi * xi);
        screening_sites_ = xi * device_.mesh.sites;
        screening_edge_centers_ = xi * device_.mesh.edge_mesh->centers;
        A_induced_ = MatrixX2d::Zero(ne, 2);
    }

    dt_ = options_.dt_init;

    if (!output_path.empty()) {
        solution_writer_ = std::make_unique<SolutionWriter>(output_path, device_.mesh);
    }
}

void TdglSolver::update_mu_boundary(double j_scale) {
    mu_boundary_.setZero();

    for (auto& term : device_.terminals) {
        double other_sum = 0.0;
        for (auto& other : device_.terminals) {
            if (other.name != term.name) {
                auto it = terminal_currents_.find(other.name);
                if (it != terminal_currents_.end())
                    other_sum += it->second;
            }
        }
        double current_density = (-1.0 / term.length) * other_sum * j_scale;

        for (int i = 0; i < term.boundary_edge_indices.size(); ++i) {
            int bei = term.boundary_edge_indices(i);
            if (bei >= 0 && bei < mu_boundary_.size()) {
                mu_boundary_(bei) = current_density;
            }
        }
    }
}

void TdglSolver::solve() {
    int step = 0;
    int max_steps = static_cast<int>(options_.solve_time / options_.dt_init) * 10;
    max_steps = std::max(max_steps, 10000);

    std::deque<double> d_psi_sq_history;
    int save_counter = 0;

    VectorXd dA_dt = VectorXd::Zero(device_.mesh.edge_mesh->num_edges);

    double tentative_dt = dt_;
    double dt_max = options_.adaptive ? options_.dt_max : options_.dt_init;

    // Save initial state
    if (solution_writer_) {
        solution_writer_->save_step(0, 0.0, dt_,
            psi_, mu_, supercurrent_, normal_current_,
            applied_A_, A_induced_, epsilon_);
    }

    for (step = 0; step < max_steps && time_ < options_.solve_time; ++step) {
        dt_ = tentative_dt;

        VectorXd old_sq_psi = psi_.array().abs().square();

        // Screening loop (runs once if screening disabled)
        double screening_error = std::numeric_limits<double>::infinity();
        MatrixX2d velocity = MatrixX2d::Zero(device_.mesh.edge_mesh->num_edges, 2);
        VectorXd new_sq_psi = old_sq_psi;

        for (int screening_iter = 0; ; ++screening_iter) {
            if (screening_error < options_.screening_tolerance) break;
            if (screening_iter > options_.max_iterations_per_step) {
                throw std::runtime_error(
                    "Screening failed to converge at step " + std::to_string(step) +
                    " after " + std::to_string(options_.max_iterations_per_step) +
                    " iterations. Error: " + std::to_string(screening_error));
            }

            if (screening_iter == 0) {
                dt_ = tentative_dt;
            }

            if (include_screening_) {
                operators_.set_link_exponents(applied_A_ + A_induced_);
            }

            auto result = adaptive_euler_step(
                step, psi_, old_sq_psi, mu_, epsilon_,
                dt_, options_, gamma_, u_, operators_.psi_laplacian_);

            psi_ = result.psi;
            new_sq_psi = result.abs_sq_psi;
            dt_ = result.dt;

            auto obs = solve_for_observables(
                psi_, dA_dt, operators_, mu_boundary_, poisson_solver_);
            mu_ = obs.mu;
            supercurrent_ = obs.supercurrent;
            normal_current_ = obs.normal_current;

            if (include_screening_) {
                auto& edges = device_.mesh.edge_mesh->edges;
                auto& norm_dirs = device_.mesh.edge_mesh->normalized_directions;

                // J_site = interpolate (J_s + J_n) from edges to sites
                MatrixX2d J_site = interpolate_vector_edges_to_sites(
                    supercurrent_ + normal_current_, edges, norm_dirs,
                    device_.mesh.num_sites);

                // Compute new A_induced via Biot-Savart
                MatrixX2d new_A = compute_induced_vector_potential(
                    J_site, screening_areas_,
                    screening_sites_,
                    screening_edge_centers_);

                // Polyak heavy ball update
                double alpha = options_.screening_step_size;
                double beta = options_.screening_step_drag;
                MatrixX2d dA = new_A - A_induced_;
                velocity = (1.0 - beta) * velocity + alpha * dA;
                A_induced_ = A_induced_ + velocity;

                // Convergence: max over edges of |dA| / |A|
                VectorXd numerator = dA.rowwise().norm();
                VectorXd denominator = A_induced_.rowwise().norm();
                for (int i = 0; i < denominator.size(); ++i) {
                    denominator(i) = std::max(denominator(i), 1e-20);
                }
                screening_error = (numerator.array() / denominator.array()).maxCoeff();
            } else {
                break;
            }
        }

        // Adaptive dt
        if (options_.adaptive) {
            double d_psi_sq = (new_sq_psi - old_sq_psi).cwiseAbs().maxCoeff();
            d_psi_sq_history.push_back(d_psi_sq);

            int window = options_.adaptive_window;
            if (step > window) {
                double avg = 0;
                int count = 0;
                for (auto it = d_psi_sq_history.end() - window;
                     it != d_psi_sq_history.end(); ++it) {
                    avg += *it;
                    count++;
                }
                avg /= count;
                double new_dt = options_.dt_init / std::max(1e-10, avg);
                tentative_dt = std::max(0.0, std::min(0.5 * (new_dt + dt_), dt_max));
            }
        }

        time_ += dt_;

        // Save if needed
        save_counter++;
        if (options_.save_every > 0 && save_counter >= options_.save_every) {
            save_counter = 0;
            if (solution_writer_) {
                solution_writer_->save_step(step, time_, dt_,
                    psi_, mu_, supercurrent_, normal_current_,
                    applied_A_, A_induced_, epsilon_);
            }
        }

        if (step > 0 && step % 100 == 0) {
            std::cout << "  Step " << step << ": time=" << time_
                      << ", dt=" << dt_
                      << ", |psi|^2 in [" << new_sq_psi.minCoeff()
                      << ", " << new_sq_psi.maxCoeff() << "]\n";
        }
    }

    // Save final state
    if (solution_writer_) {
        solution_writer_->save_step(step, time_, dt_,
            psi_, mu_, supercurrent_, normal_current_,
            applied_A_, A_induced_, epsilon_);
    }

    std::cout << "Solve complete: " << step << " steps, time=" << time_
              << ", dt=" << dt_ << "\n";
}
