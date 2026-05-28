// Minimal step profiler
#include "mesh/io.h"
#include "device/device.h"
#include "options/options.h"
#include "operators/operators.h"
#include "operators/poisson_solver.h"
#include "solver/psi_update.h"
#include "solver/observables.h"
#include "solver/adaptive_step.h"
#include <iostream>
#include <chrono>
#include <numeric>
#include <vector>

using Clock = std::chrono::high_resolution_clock;

int main() {
    std::cerr << "Reading mesh..." << std::endl;
    auto mesh = read_mesh("/mnt/c/Users/photo/Photonics_Group/Ruihuan/cpp-tdgl/data/weak_link_no_screen.h5");
    std::cerr << "Mesh done" << std::endl;

    auto device = read_device("/mnt/c/Users/photo/Photonics_Group/Ruihuan/cpp-tdgl/data/weak_link_no_screen.h5");
    std::cerr << "Device done" << std::endl;

    auto options = read_options("/mnt/c/Users/photo/Photonics_Group/Ruihuan/cpp-tdgl/data/weak_link_no_screen.h5");
    std::cerr << "Options done" << std::endl;

    int ns = mesh.num_sites;
    int ne = mesh.edge_mesh->num_edges;
    double gamma = device.layer.gamma;
    double u = device.layer.u;
    double dt = options.dt_max;
    std::cerr << "Setup done, running " << ns << " sites" << std::endl;

    Eigen::VectorXi fixed_sites(0);
    int total_fixed = 0;
    for (auto& t : device.terminals) total_fixed += t.site_indices.size();
    fixed_sites.resize(total_fixed);
    int idx = 0;
    for (auto& t : device.terminals)
        for (int i = 0; i < t.site_indices.size(); ++i)
            fixed_sites(idx++) = t.site_indices(i);

    std::cerr << "Building operators..." << std::endl;
    MeshOperators ops;
    ops.build_operators(mesh, fixed_sites, fixed_sites.size() > 0);
    std::cerr << "Operators done" << std::endl;

    std::cerr << "Factorizing..." << std::endl;
    PoissonSolver ps;
    ps.factorize(ops.mu_laplacian_);
    std::cerr << "Factorized" << std::endl;

    Eigen::VectorXcd psi = Eigen::VectorXcd::Ones(ns);
    for (int i = 0; i < fixed_sites.size(); ++i)
        psi(fixed_sites(i)) = std::complex<double>(0, 0);
    Eigen::VectorXd abs_sq_psi = psi.array().abs().square();
    Eigen::VectorXd mu = Eigen::VectorXd::Zero(ns);
    Eigen::VectorXd epsilon = Eigen::VectorXd::Ones(ns);
    Eigen::VectorXd dA_dt = Eigen::VectorXd::Zero(ne);
    Eigen::VectorXd mu_boundary = Eigen::VectorXd::Zero(mesh.edge_mesh->boundary_edge_indices.size());

    std::cerr << "Warming up..." << std::endl;
    for (int i = 0; i < 5; ++i) {
        auto result = solve_for_psi_squared(psi, abs_sq_psi, mu, epsilon,
                                             gamma, u, dt, ops.psi_laplacian_);
        if (result.has_value()) { psi = result->psi; abs_sq_psi = result->abs_sq_psi; }
        auto obs = solve_for_observables(psi, dA_dt, ops, mu_boundary, ps);
        mu = obs.mu;
    }
    std::cerr << "Warmup done" << std::endl;

    // Profile 10 steps
    const int N = 10;
    std::vector<double> t_psi(N), t_obs(N);
    int retries_total = 0;

    for (int i = 0; i < N; ++i) {
        auto t0 = Clock::now();
        int retries = 0;
        double cur_dt = dt;
        while (retries <= 10) {
            auto result = solve_for_psi_squared(psi, abs_sq_psi, mu, epsilon,
                                                 gamma, u, cur_dt, ops.psi_laplacian_);
            if (result.has_value()) { psi = result->psi; abs_sq_psi = result->abs_sq_psi; break; }
            cur_dt *= 0.25;
            retries++;
        }
        retries_total += retries;
        auto t1 = Clock::now();
        t_psi[i] = std::chrono::duration<double, std::milli>(t1 - t0).count();

        auto t2 = Clock::now();
        auto obs = solve_for_observables(psi, dA_dt, ops, mu_boundary, ps);
        mu = obs.mu;
        auto t3 = Clock::now();
        t_obs[i] = std::chrono::duration<double, std::milli>(t3 - t2).count();
    }

    double mean_psi = std::accumulate(t_psi.begin(), t_psi.end(), 0.0) / N;
    double mean_obs = std::accumulate(t_obs.begin(), t_obs.end(), 0.0) / N;
    double max_psi = *std::max_element(t_psi.begin(), t_psi.end());
    double max_obs = *std::max_element(t_obs.begin(), t_obs.end());

    std::cerr << std::fixed << std::setprecision(3);
    std::cerr << "=== Profile (" << N << " steps) ===\n";
    std::cerr << "  adaptive_euler: mean=" << mean_psi << " max=" << max_psi << " ms\n";
    std::cerr << "  observables:    mean=" << mean_obs << " max=" << max_obs << " ms\n";
    std::cerr << "  total/step:    " << (mean_psi + mean_obs) << " ms\n";
    std::cerr << "  retries:       " << retries_total << "\n";

    // Decompose observables
    std::cerr << "\n--- Decomposing observables ---\n";

    // Time divergence_ * supercurrent (real sparse matvec)
    auto J = ops.get_supercurrent(psi);
    Eigen::VectorXd rhs = ops.divergence_ * J;
    rhs -= ops.mu_boundary_laplacian_ * mu_boundary;
    std::cerr << "  divergence * J: computed rhs\n";

    // Time just the Poisson solve
    const int M = 100;
    auto t2 = Clock::now();
    for (int i = 0; i < M; ++i) ps.solve(rhs);
    auto t3 = Clock::now();
    double ms_solve = std::chrono::duration<double, std::milli>(t3 - t2).count() / M;
    std::cerr << "  Poisson solve: " << ms_solve << " ms\n";

    // Time get_supercurrent
    t2 = Clock::now();
    for (int i = 0; i < M; ++i) ops.get_supercurrent(psi);
    t3 = Clock::now();
    double ms_sc = std::chrono::duration<double, std::milli>(t3 - t2).count() / M;
    std::cerr << "  get_supercurrent: " << ms_sc * 1000 << " us\n";

    // Time psi_gradient_ * psi (complex sparse matvec)
    t2 = Clock::now();
    for (int i = 0; i < M; ++i) ops.psi_gradient_ * psi;
    t3 = Clock::now();
    double ms_pgrad = std::chrono::duration<double, std::milli>(t3 - t2).count() / M;
    std::cerr << "  psi_gradient * psi: " << ms_pgrad * 1000 << " us\n";

    // Time divergence_ * J
    Eigen::VectorXd ones_ne = Eigen::VectorXd::Ones(ne);
    t2 = Clock::now();
    for (int i = 0; i < M; ++i) ops.divergence_ * ones_ne;
    t3 = Clock::now();
    double ms_div = std::chrono::duration<double, std::milli>(t3 - t2).count() / M;
    std::cerr << "  divergence * ones: " << ms_div * 1000 << " us\n";

    // Time mu_boundary_laplacian * mu_boundary
    t2 = Clock::now();
    for (int i = 0; i < M; ++i) ops.mu_boundary_laplacian_ * mu_boundary;
    t3 = Clock::now();
    double ms_bnd = std::chrono::duration<double, std::milli>(t3 - t2).count() / M;
    std::cerr << "  mu_boundary_laplacian * mu_boundary: " << ms_bnd * 1000 << " us\n";

    // Time -mu_gradient * mu
    t2 = Clock::now();
    for (int i = 0; i < M; ++i) -(ops.mu_gradient_ * mu);
    t3 = Clock::now();
    double ms_grad = std::chrono::duration<double, std::milli>(t3 - t2).count() / M;
    std::cerr << "  mu_gradient * mu: " << ms_grad * 1000 << " us\n";

    // Time normal_current = -grad*mu - dA_dt
    t2 = Clock::now();
    for (int i = 0; i < M; ++i) { -(ops.mu_gradient_ * mu) - dA_dt; }
    t3 = Clock::now();
    double ms_norm = std::chrono::duration<double, std::milli>(t3 - t2).count() / M;
    std::cerr << "  normal_current: " << ms_norm * 1000 << " us\n";

    // Time full observables
    t2 = Clock::now();
    for (int i = 0; i < M; ++i) solve_for_observables(psi, dA_dt, ops, mu_boundary, ps);
    t3 = Clock::now();
    double ms_full = std::chrono::duration<double, std::milli>(t3 - t2).count() / M;
    std::cerr << "  full observables: " << ms_full << " ms\n";

    // Summary: residual = full - sum(parts)
    double ms_parts = (ms_sc + ms_pgrad + ms_div + ms_bnd + ms_grad + ms_solve + ms_norm) * 1000;  // us
    double ms_full_us = ms_full * 1000;
    std::cerr << "\n--- Summary ---\n";
    std::cerr << "  full_observables: " << ms_full_us << " us\n";
    std::cerr << "  sum of parts:     " << ms_parts << " us\n";
    std::cerr << "  unaccounted:      " << (ms_full_us - ms_parts) << " us\n";

    return 0;
}
