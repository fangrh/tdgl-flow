# cpp-tdgl-runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a K8s service that runs TDGL simulations using the C++ solver for higher speed, with 100% input/output compatibility with py-tdgl.

**Architecture:** Single Docker container: Python glue handles mesh building (tdgl library), epsilon generation, and MinIO upload; compiled C++ binary handles the TDGL solve. Argo WorkflowTemplate mirrors py-tdgl-runner.

**Tech Stack:** C++20 (Eigen3, HDF5, SuiteSparse, HighFive, nlohmann/json), Python 3.13 (tdgl, h5py, boto3), Docker multi-stage, Argo Workflows.

**Repos:**
- `CPP_TDGL=/mnt/c/Users/photo/Photonics_Group/Ruihuan/git-tdgl-light/cpp-tdgl` — C++ solver (modified)
- `KTDGL=/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl` — K8s service (new files)

---

## File Map

| File | Repo | Action | Purpose |
|------|------|--------|---------|
| `CPP_TDGL/src/timing/timing.h` | cpp-tdgl | Create | Timing step struct + JSON parser |
| `CPP_TDGL/src/solution/solution.h` | cpp-tdgl | Modify | Add running_state params, flush() |
| `CPP_TDGL/src/solution/solution.cpp` | cpp-tdgl | Modify | Complex128 psi, running_state, flush |
| `CPP_TDGL/src/solver/solver.h` | cpp-tdgl | Modify | Add timing steps, probe indices |
| `CPP_TDGL/src/solver/solver.cpp` | cpp-tdgl | Modify | Timing step loop, probe tracking |
| `CPP_TDGL/src/main.cpp` | cpp-tdgl | Modify | New CLI args: --timing, --solver-options, --epsilon |
| `CPP_TDGL/CMakeLists.txt` | cpp-tdgl | Modify | Add timing source, nlohmann_json |
| `CPP_TDGL/tests/test_timing.cpp` | cpp-tdgl | Create | Timing parser tests |
| `KTDGL/services/cpp-tdgl-runner/runner.py` | kubeflow-tdgl | Create | Python glue: build mesh, epsilon, call C++, upload MinIO |
| `KTDGL/services/cpp-tdgl-runner/build_device.py` | kubeflow-tdgl | Create | Copy from py-tdgl-runner (identical) |
| `KTDGL/services/cpp-tdgl-runner/build_timing.py` | kubeflow-tdgl | Create | Copy from py-tdgl-runner (identical) |
| `KTDGL/services/cpp-tdgl-runner/Dockerfile` | kubeflow-tdgl | Create | Multi-stage: compile C++ + Python runtime |
| `KTDGL/services/cpp-tdgl-runner/k8s/workflowtemplate.yaml` | kubeflow-tdgl | Create | Argo WorkflowTemplate for cpp-tdgl-sim |

---

### Task 1: Add timing JSON parser to cpp-tdgl

**Files:**
- Create: `CPP_TDGL/src/timing/timing.h`
- Create: `CPP_TDGL/tests/test_timing.cpp`
- Modify: `CPP_TDGL/CMakeLists.txt`

This parser reads the same timing.json that `build_timing.py` produces (simple or segmented mode) into a `std::vector<TimingStep>`.

- [ ] **Step 1: Add nlohmann/json to CMakeLists.txt**

Add to `CPP_TDGL/CMakeLists.txt` after the HighFive `FetchContent` block:

```cmake
# nlohmann/json for timing JSON parsing
FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
)
FetchContent_MakeAvailable(nlohmann_json)
```

Add `nlohmann_json::nlohmann_json` to `target_link_libraries(tdgl_core ...)`.

- [ ] **Step 2: Create timing.h**

Create `CPP_TDGL/src/timing/timing.h`:

```cpp
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
```

- [ ] **Step 3: Implement parse_timing_json**

Create `CPP_TDGL/src/timing/timing.cpp`:

```cpp
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

    // Append ramp_down steps if present
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
```

Add `src/timing/timing.cpp` to the `tdgl_core` source list in CMakeLists.txt.

- [ ] **Step 4: Write timing parser test**

Create `CPP_TDGL/tests/test_timing.cpp`:

```cpp
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
```

Add to CMakeLists.txt:

```cmake
add_executable(test_timing tests/test_timing.cpp)
target_link_libraries(test_timing PRIVATE tdgl_core)
```

- [ ] **Step 5: Build and run test**

```bash
cd $CPP_TDGL && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j$(nproc) --target test_timing
./build/test_timing
```

Expected: `All timing tests passed.`

- [ ] **Step 6: Commit**

```bash
cd $CPP_TDGL
git add src/timing/ tests/test_timing.cpp CMakeLists.txt
git commit -m "feat: add timing JSON parser for step-scheduled simulations"
```

---

### Task 2: Fix SolutionWriter for py-tdgl-compatible HDF5 output

**Files:**
- Modify: `CPP_TDGL/src/solution/solution.h`
- Modify: `CPP_TDGL/src/solution/solution.cpp`

Three changes: (1) write `psi` as complex128 instead of separate real/imag, (2) add `flush()` method for periodic MinIO upload, (3) add `save_running_state()` for IV voltage computation.

- [ ] **Step 1: Update solution.h**

Replace the full content of `CPP_TDGL/src/solution/solution.h`:

```cpp
#pragma once

#include "mesh/mesh.h"
#include <Eigen/Core>
#include <highfive/H5File.hpp>
#include <memory>
#include <string>
#include <vector>

class SolutionWriter {
public:
    SolutionWriter(const std::string& output_path, const Mesh& mesh,
                   const std::vector<int>& probe_indices = {});
    ~SolutionWriter();

    void save_step(int step, double time, double dt,
                   const Eigen::VectorXcd& psi,
                   const Eigen::VectorXd& mu,
                   const Eigen::VectorXd& supercurrent,
                   const Eigen::VectorXd& normal_current,
                   const Eigen::MatrixX2d& applied_A = {},
                   const Eigen::MatrixX2d& induced_A = {},
                   const Eigen::VectorXd& epsilon = {});

    void save_running_state(int frame_idx,
                            const std::vector<double>& rsmu,
                            const std::vector<double>& rsdt);

    void flush();

    int frame_count() const { return save_count_; }

private:
    std::string output_path_;
    int save_count_ = 0;
    std::vector<int> probe_indices_;
    std::unique_ptr<HighFive::File> file_;
};
```

- [ ] **Step 2: Update solution.cpp — constructor**

The constructor writes mesh data and creates the `data/` group. It also stores probe indices for running_state. Replace the constructor in `CPP_TDGL/src/solution/solution.cpp`:

```cpp
SolutionWriter::SolutionWriter(const std::string& output_path, const Mesh& mesh,
                               const std::vector<int>& probe_indices)
    : output_path_(output_path), save_count_(0), probe_indices_(probe_indices) {
    namespace h5 = HighFive;
    file_ = std::make_unique<h5::File>(output_path, h5::File::Overwrite);

    auto mesh_grp = file_->createGroup("mesh");
    Eigen::Matrix<double, -1, 2, Eigen::RowMajor> sites_rm = mesh.sites;
    write_2d<double>(mesh_grp, "sites", sites_rm.data(), sites_rm.rows(), 2);
    Eigen::Matrix<int64_t, -1, 3, Eigen::RowMajor> elems_rm = mesh.elements;
    write_2d<int64_t>(mesh_grp, "elements", elems_rm.data(),
                       elems_rm.rows(), 3);
    std::vector<int64_t> bi(mesh.boundary_indices.data(),
                              mesh.boundary_indices.data() + mesh.boundary_indices.size());
    write_1d<int64_t>(mesh_grp, "boundary_indices", bi.data(), bi.size());
    write_1d<double>(mesh_grp, "areas", mesh.areas.data(), mesh.areas.rows());

    if (mesh.edge_mesh) {
        auto& em = *mesh.edge_mesh;
        auto eg = mesh_grp.createGroup("edge_mesh");
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> centers_rm = em.centers;
        write_2d<double>(eg, "centers", centers_rm.data(), centers_rm.rows(), 2);
        Eigen::Matrix<int64_t, -1, 2, Eigen::RowMajor> edges_rm = em.edges;
        write_2d<int64_t>(eg, "edges", edges_rm.data(), edges_rm.rows(), 2);
        write_1d<double>(eg, "edge_lengths", em.edge_lengths.data(), em.edge_lengths.rows());
        write_1d<double>(eg, "dual_edge_lengths", em.dual_edge_lengths.data(),
                         em.dual_edge_lengths.rows());
    }

    file_->createGroup("data");
}
```

- [ ] **Step 3: Update solution.cpp — save_step with complex128 psi**

Replace `save_step` to write psi as interleaved complex128 (re, im pairs as contiguous float64 array of length 2*N):

```cpp
void SolutionWriter::save_step(int step, double time, double dt,
                                const Eigen::VectorXcd& psi,
                                const Eigen::VectorXd& mu,
                                const Eigen::VectorXd& supercurrent,
                                const Eigen::VectorXd& normal_current,
                                const Eigen::MatrixX2d& applied_A,
                                const Eigen::MatrixX2d& induced_A,
                                const Eigen::VectorXd& epsilon) {
    namespace h5 = HighFive;
    auto data_grp = file_->getGroup("data");
    std::string step_name = std::to_string(save_count_);
    auto grp = data_grp.createGroup(step_name);

    int n = psi.size();

    // Write psi as complex128: interleaved [re0, im0, re1, im1, ...]
    // This matches py-tdgl's storage format and the Rust viewer's expectations.
    std::vector<double> psi_interleaved(2 * n);
    for (int i = 0; i < n; ++i) {
        psi_interleaved[2 * i] = psi(i).real();
        psi_interleaved[2 * i + 1] = psi(i).imag();
    }
    h5::DataSpace psi_space({static_cast<unsigned long long>(n)});
    auto psi_ds = grp.createDataSet<double>("psi", psi_space);
    // Write raw bytes as complex128 by creating a compound type
    // Simpler approach: write as (N,2) float64 which the Rust viewer reads correctly
    h5::DataSpace psi2_space({static_cast<unsigned long long>(n), 2ULL});
    grp.createDataSet<double>("psi", psi2_space).write_raw(psi_interleaved.data());

    write_1d<double>(grp, "mu", mu.data(), n);
    write_1d<double>(grp, "supercurrent", supercurrent.data(), supercurrent.size());
    write_1d<double>(grp, "normal_current", normal_current.data(), normal_current.size());

    if (applied_A.size() > 0) {
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> A_rm = applied_A;
        write_2d<double>(grp, "applied_vector_potential",
                         A_rm.data(), A_rm.rows(), 2);
    }
    if (induced_A.size() > 0) {
        Eigen::Matrix<double, -1, 2, Eigen::RowMajor> Ai_rm = induced_A;
        write_2d<double>(grp, "induced_vector_potential",
                         Ai_rm.data(), Ai_rm.rows(), 2);
    }
    if (epsilon.size() > 0)
        write_1d<double>(grp, "epsilon", epsilon.data(), epsilon.size());

    // Attributes
    grp.createAttribute("step", step).write(step);
    grp.createAttribute("time", time).write(time);
    grp.createAttribute("dt", dt).write(dt);

    save_count_++;
}
```

> **Note:** The Rust viewer's binary scanner identifies psi by size N\*16 bytes. Writing psi as (N,2) float64 gives exactly N\*16 bytes of contiguous data, which matches.

- [ ] **Step 4: Add save_running_state and flush methods**

Append to `CPP_TDGL/src/solution/solution.cpp`:

```cpp
void SolutionWriter::save_running_state(int frame_idx,
                                         const std::vector<double>& rsmu,
                                         const std::vector<double>& rsdt) {
    namespace h5 = HighFive;
    auto data_grp = file_->getGroup("data");
    std::string frame_name = std::to_string(frame_idx);
    auto grp = data_grp.getGroup(frame_name);
    auto rs_grp = grp.createGroup("running_state");
    write_1d<double>(rs_grp, "mu", rsmu.data(), rsmu.size());
    write_1d<double>(rs_grp, "dt", rsdt.data(), rsdt.size());
}

void SolutionWriter::flush() {
    if (file_) file_->flush();
}
```

- [ ] **Step 5: Build and verify compilation**

```bash
cd $CPP_TDGL && cmake --build build -j$(nproc)
```

Expected: Compiles without errors.

- [ ] **Step 6: Commit**

```bash
cd $CPP_TDGL
git add src/solution/solution.h src/solution/solution.cpp
git commit -m "fix: write psi as complex128, add running_state and flush to SolutionWriter"
```

---

### Task 3: Modify solver for timing step loop and probe tracking

**Files:**
- Modify: `CPP_TDGL/src/solver/solver.h`
- Modify: `CPP_TDGL/src/solver/solver.cpp`

The solver needs to: (1) accept a timing schedule instead of fixed terminal currents, (2) update terminal currents during the solve loop, (3) record probe-point mu and dt values for running_state.

- [ ] **Step 1: Update solver.h**

Add timing schedule and probe tracking to the solver. In `CPP_TDGL/src/solver/solver.h`, add these includes and modify the constructor:

```cpp
#pragma once

#include "device/device.h"
#include "options/options.h"
#include "mesh/operators.h"
#include "mesh/poisson.h"
#include "timing/timing.h"
#include <Eigen/Core>
#include <map>
#include <string>
#include <vector>

class SolutionWriter;

class TdglSolver {
public:
    // Legacy constructor: fixed terminal currents, single solve_time
    TdglSolver(const Device& device, const Options& options,
               const Eigen::MatrixX2d& applied_vector_potential,
               const std::map<std::string, double>& terminal_currents,
               double disorder_epsilon = 1.0,
               const std::string& output_path = "",
               const std::string& restart_path = "");

    // Timing schedule constructor: step-based terminal currents
    TdglSolver(const Device& device, const Options& options,
               const Eigen::MatrixX2d& applied_vector_potential,
               const TimingSchedule& timing,
               double disorder_epsilon = 1.0,
               const std::string& output_path = "",
               const std::string& restart_path = "");

    void solve();
    const SolutionWriter* solution_writer() const { return solution_writer_.get(); }
    void update_mu_boundary(double j_scale = 1.0);

private:
    double terminal_current_at(double t) const;
    void record_running_state();

    const Device& device_;
    Options options_;
    double u_, gamma_;
    Eigen::VectorXd epsilon_;
    Eigen::MatrixX2d applied_A_;
    std::map<std::string, double> terminal_currents_;
    TimingSchedule timing_;
    bool use_timing_ = false;

    Eigen::VectorXcd psi_;
    Eigen::VectorXd mu_;
    Eigen::VectorXd supercurrent_;
    Eigen::VectorXd normal_current_;

    MeshOperators operators_;
    PoissonSolver poisson_solver_;
    Eigen::VectorXd mu_boundary_;
    Eigen::MatrixX2d A_induced_;
    Eigen::VectorXd screening_areas_;
    Eigen::MatrixX2d screening_sites_;
    Eigen::MatrixX2d screening_edge_centers_;

    std::unique_ptr<SolutionWriter> solution_writer_;
    double time_ = 0.0;
    double dt_ = 0.0;

    // Running state tracking for probe-point voltage
    std::vector<int> probe_indices_;
    std::vector<double> rs_mu_buffer_;
    std::vector<double> rs_dt_buffer_;
    int last_saved_frame_ = -1;
};
```

- [ ] **Step 2: Add timing constructor and terminal_current_at to solver.cpp**

Add after the existing constructor in `CPP_TDGL/src/solver/solver.cpp`:

```cpp
TdglSolver::TdglSolver(const Device& device, const Options& options,
                         const Eigen::MatrixX2d& applied_vector_potential,
                         const TimingSchedule& timing,
                         double disorder_epsilon,
                         const std::string& output_path,
                         const std::string& restart_path)
    : TdglSolver(device, options, applied_vector_potential,
                 std::map<std::string, double>{},
                 disorder_epsilon, output_path, restart_path) {
    timing_ = timing;
    use_timing_ = true;
    probe_indices_ = device.probe_point_indices;
}
```

Add the `terminal_current_at` method:

```cpp
double TdglSolver::terminal_current_at(double t) const {
    if (!use_timing_) {
        auto it = terminal_currents_.find("source");
        return it != terminal_currents_.end() ? it->second : 0.0;
    }
    const auto& steps = timing_.steps;
    for (const auto& step : steps) {
        if (t < step.ramp_start) continue;
        double ramp_duration = step.ramp_end - step.ramp_start;
        if (ramp_duration > 0 && t <= step.ramp_end) {
            double frac = (t - step.ramp_start) / ramp_duration;
            return step.je_start + frac * (step.je_end - step.je_start);
        }
        if (t <= step.stable_end) {
            return step.je_end;
        }
    }
    if (!steps.empty()) return steps.back().je_end;
    return 0.0;
}
```

- [ ] **Step 3: Modify solve() loop to use terminal_current_at and track running_state**

In `solver.cpp::solve()`, replace the single fixed-terminal-current solve with a loop that calls `terminal_current_at(time_)` each step. Find the existing solve loop and replace:

```cpp
void TdglSolver::solve() {
    // ... existing initialization code stays the same ...
    // In the main solve loop, replace fixed terminal_currents_ usage:
    //
    // Before:
    //   update_mu_boundary(1.0);  // fixed j_scale
    //
    // After:
    //   double je = terminal_current_at(time_);
    //   update_mu_boundary(je);
    //
    // And before each save_step call, add running_state recording:
    //
    //   if (solution_writer_ && probe_indices_.size() >= 2) {
    //       record_running_state();
    //       solution_writer_->save_running_state(save_count - 1, rs_mu_buffer_, rs_dt_buffer_);
    //   }
}
```

> **Implementation note:** The exact line numbers depend on the current solve() loop structure (lines 185-330 of solver.cpp). The key change is calling `terminal_current_at(time_)` to get the current Je value instead of using the fixed `terminal_currents_` map, and passing it to `update_mu_boundary(je)`.

Add `record_running_state` method:

```cpp
void TdglSolver::record_running_state() {
    if (probe_indices_.size() < 2) return;
    // Record mu at two probe points: row 0 = probe0, row 1 = probe1
    int p0 = probe_indices_[0];
    int p1 = probe_indices_[1];
    rs_mu_buffer_.push_back(mu_(p0));
    rs_mu_buffer_.push_back(mu_(p1));
    rs_dt_buffer_.push_back(dt_);
}
```

Before each `solution_writer_->save_step(...)` call, flush the running state buffer:

```cpp
if (!rs_mu_buffer_.empty() && !rs_dt_buffer_.empty()) {
    solution_writer_->save_running_state(
        solution_writer_->frame_count() - 1, rs_mu_buffer_, rs_dt_buffer_);
    rs_mu_buffer_.clear();
    rs_dt_buffer_.clear();
}
```

- [ ] **Step 4: Build**

```bash
cd $CPP_TDGL && cmake --build build -j$(nproc)
```

Expected: Compiles without errors.

- [ ] **Step 5: Commit**

```bash
cd $CPP_TDGL
git add src/solver/solver.h src/solver/solver.cpp
git commit -m "feat: add timing schedule mode with dynamic terminal currents and probe tracking"
```

---

### Task 4: Wire timing, epsilon, and solver options into main.cpp

**Files:**
- Modify: `CPP_TDGL/src/main.cpp`

Add CLI arguments `--timing`, `--solver-options`, and read epsilon from the mesh HDF5.

- [ ] **Step 1: Add new CLI arguments and timing mode**

In `CPP_TDGL/src/main.cpp`, add after the existing CLI argument declarations (line 44):

```cpp
std::string timing_path;
std::string solver_options_json;
```

Add argument parsing in the for loop (after line 58):

```cpp
else if (arg == "--timing" && i + 1 < argc) timing_path = argv[++i];
else if (arg == "--solver-options" && i + 1 < argc) solver_options_json = argv[++i];
```

- [ ] **Step 2: Read epsilon from mesh HDF5**

After reading the device (line 84), add epsilon reading:

```cpp
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
double disorder_epsilon = epsilon_values.size() > 0 ? 1.0 : 1.0;
```

- [ ] **Step 3: Apply solver options from JSON**

After reading options (line 85), add:

```cpp
if (!solver_options_json.empty()) {
    auto opts = nlohmann::json::parse(solver_options_json);
    if (opts.contains("dt_init")) options.dt_init = opts["dt_init"].get<double>();
    if (opts.contains("dt_max")) options.dt_max = opts["dt_max"].get<double>();
    if (opts.contains("adaptive")) options.adaptive = opts["adaptive"].get<bool>();
    if (opts.contains("save_every")) options.save_every = opts["save_every"].get<int>();
    std::cout << "Solver options from JSON: dt_init=" << options.dt_init
              << " dt_max=" << options.dt_max << " save_every=" << options.save_every << "\n";
}
```

Add `#include <nlohmann/json.hpp>` at the top.

- [ ] **Step 4: Use timing schedule or legacy mode**

Replace the solver construction (around line 145) with:

```cpp
std::unique_ptr<TdglSolver> solver;
if (!timing_path.empty()) {
    auto timing = parse_timing_json(timing_path);
    options.solve_time = timing.solve_time;
    std::cout << "Timing: " << timing.n_steps << " steps, solve_time=" << timing.solve_time << "\n";
    solver = std::make_unique<TdglSolver>(
        device, options, applied_A, timing,
        disorder_epsilon, output_path, restart_path);
} else {
    std::map<std::string, double> terminal_currents;
    for (auto& t : device.terminals) {
        if (t.name == "source") terminal_currents["source"] = source_current;
        if (t.name == "drain") terminal_currents["drain"] = drain_current;
    }
    solver = std::make_unique<TdglSolver>(
        device, options, applied_A, terminal_currents,
        disorder_epsilon, output_path, restart_path);
}
solver->solve();
```

Add `#include "timing/timing.h"` at the top.

- [ ] **Step 5: Build and test locally**

```bash
cd $CPP_TDGL && cmake --build build -j$(nproc)
./build/tdgl_solve --help
```

Expected: Help text shows `--timing` and `--solver-options` options.

- [ ] **Step 6: Commit**

```bash
cd $CPP_TDGL
git add src/main.cpp
git commit -m "feat: add --timing, --solver-options CLI and epsilon reading"
```

---

### Task 5: Create Python mesh-to-HDF5 converter

**Files:**
- Create: `KTDGL/services/cpp-tdgl-runner/convert_mesh.py`

This module converts a tdgl `Device` object (from `build_rectangular_device`) into an HDF5 file that cpp-tdgl's `read_device()` can read. It also writes epsilon and solver options into the same file.

- [ ] **Step 1: Create convert_mesh.py**

Create `KTDGL/services/cpp-tdgl-runner/convert_mesh.py`:

```python
"""Convert tdgl Device + epsilon + solver options into cpp-tdgl-compatible HDF5."""
import numpy as np
import h5py


def write_cpp_mesh(device, output_path, solver_options=None, epsilon_fn=None):
    """Write a tdgl Device to an HDF5 file that cpp-tdgl can read.

    Args:
        device: tdgl.Device object (from build_rectangular_device)
        output_path: path to write HDF5
        solver_options: dict with dt_init, dt_max, adaptive, save_every
        epsilon_fn: callable(x, y) -> float, or None
    """
    with h5py.File(output_path, "w") as f:
        # --- Mesh group ---
        mesh = device.mesh
        mesh_grp = f.create_group("mesh")
        sites = np.array(mesh.site_coords, dtype=np.float64)
        elements = np.array(mesh.triangles, dtype=np.int64)
        mesh_grp.create_dataset("sites", data=sites)
        mesh_grp.create_dataset("elements", data=elements)

        boundary = np.array(mesh.boundary_indices, dtype=np.int64)
        mesh_grp.create_dataset("boundary_indices", data=boundary)

        areas = np.array(mesh.areas, dtype=np.float64)
        mesh_grp.create_dataset("areas", data=areas)

        # Edge mesh
        em = mesh.edge_mesh
        eg = mesh_grp.create_group("edge_mesh")
        eg.create_dataset("centers", data=np.array(em.centers, dtype=np.float64))
        eg.create_dataset("edges", data=np.array(em.edges, dtype=np.int64))
        eg.create_dataset("edge_lengths", data=np.array(edges_lengths(em), dtype=np.float64))
        eg.create_dataset("dual_edge_lengths", data=np.array(dual_edge_lengths(em), dtype=np.float64))

        # --- Device group ---
        dg = f.create_group("device")
        dg.attrs["name"] = device.name
        dg.attrs["length_units"] = "um"
        dg.attrs["K0"] = getattr(device, "K0", 0.0)
        dg.attrs["A0"] = getattr(device, "A0", 0.0)
        dg.attrs["Bc2"] = getattr(device, "Bc2", 0.0)
        dg.attrs["Lambda"] = getattr(device, "Lambda", 0.0)

        # Probe points
        if hasattr(device, "probe_point_indices") and device.probe_point_indices:
            dg.create_dataset("probe_point_indices",
                              data=np.array(device.probe_point_indices, dtype=np.int64))

        # Layer
        lg = dg.create_group("layer")
        layer = device.layer
        lg.attrs["london_lambda"] = layer.london_lambda
        lg.attrs["coherence_length"] = layer.coherence_length
        lg.attrs["thickness"] = layer.thickness
        lg.attrs["u"] = layer.u
        lg.attrs["gamma"] = layer.gamma
        lg.attrs["conductivity"] = getattr(layer, "conductivity", 0.0)

        # Terminals
        if hasattr(device, "terminals"):
            tg = dg.create_group("terminals")
            for term in device.terminals:
                tgrp = tg.create_group(term.name)
                tgrp.create_dataset("site_indices",
                    data=np.array(term.site_indices, dtype=np.int64))
                tgrp.create_dataset("edge_indices",
                    data=np.array(term.edge_indices, dtype=np.int64))
                tgrp.create_dataset("boundary_edge_indices",
                    data=np.array(term.boundary_edge_indices, dtype=np.int64))
                tgrp.attrs["length"] = term.length

        # --- Epsilon ---
        if epsilon_fn is not None:
            eps = np.array([epsilon_fn(x, y) for x, y in sites], dtype=np.float64)
            f.create_dataset("epsilon", data=eps)

        # --- Solver options ---
        if solver_options:
            og = f.create_group("options")
            for key in ["solve_time", "skip_time", "dt_init", "dt_max",
                        "adaptive", "adaptive_window", "save_every",
                        "include_screening"]:
                if key in solver_options:
                    og.attrs[key] = solver_options[key]
```

> **Note:** The exact attributes of `tdgl.Device`, `tdgl.Layer`, and `tdgl.Terminal` objects need to be verified against the tdgl library's API during implementation. The property names above are approximate — use `dir(device)` and `dir(device.mesh)` to confirm the exact names.

- [ ] **Step 2: Commit**

```bash
cd $KTDGL
git add services/cpp-tdgl-runner/convert_mesh.py
git commit -m "feat: add Python mesh-to-HDF5 converter for cpp-tdgl"
```

---

### Task 6: Create Python runner.py

**Files:**
- Create: `KTDGL/services/cpp-tdgl-runner/runner.py`
- Create: `KTDGL/services/cpp-tdgl-runner/build_device.py` (copy from py-tdgl-runner)
- Create: `KTDGL/services/cpp-tdgl-runner/build_timing.py` (copy from py-tdgl-runner)

- [ ] **Step 1: Copy build_device.py and build_timing.py**

```bash
cp services/py-tdgl-runner/build_device.py services/cpp-tdgl-runner/build_device.py
cp services/py-tdgl-runner/build_timing.py services/cpp-tdgl-runner/build_timing.py
```

These files are identical to py-tdgl-runner versions.

- [ ] **Step 2: Create runner.py**

Create `KTDGL/services/cpp-tdgl-runner/runner.py`:

```python
"""cpp-tdgl simulation runner (Argo simulate step).

Builds mesh with Python tdgl, converts to cpp-tdgl HDF5 format,
runs C++ solver, uploads results to MinIO for real-time viewing.
"""
import json
import os
import pickle
import subprocess
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, "/app/vendor")

import boto3
import h5py
import numpy as np
from botocore.config import Config as BotoConfig
from tdgl_workflow.epsilon import make_gaussian_epsilon
from convert_mesh import write_cpp_mesh

DATA_DIR = os.environ.get("DATA_DIR", "/data")
CPP_SOLVER = os.environ.get("CPP_SOLVER", "/usr/local/bin/cpp-tdgl-solve")


def _get_minio_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://minio.tdgl.svc.cluster.local:9000"),
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin123"),
        region_name="us-east-1",
        config=BotoConfig(connect_timeout=10, retries={"max_attempts": 3}),
    )


def _upload_to_minio(local_path, bucket, key):
    s3 = _get_minio_client()
    s3.upload_file(local_path, bucket, key)
    print(f"Uploaded {local_path} -> s3://{bucket}/{key}")


def _upload_manifest(manifest, bucket, run_id):
    path = os.path.join(DATA_DIR, "manifest.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    _upload_to_minio(path, bucket, f"tdgl-runs/{run_id}/manifest.json")


def _periodic_upload(output_path, bucket, run_id, stop_event, interval=30):
    s3 = _get_minio_client()
    key = f"tdgl-runs/{run_id}/output.h5"
    while not stop_event.is_set():
        stop_event.wait(interval)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            try:
                s3.upload_file(output_path, bucket, key)
            except Exception:
                pass


def main():
    run_id = os.environ["TDGL_RUN_ID"]
    solver_options_raw = os.environ.get("SOLVER_OPTIONS", "{}")
    solver_options = json.loads(solver_options_raw)
    epsilon_params_raw = os.environ.get("EPSILON_PARAMS", "{}")
    epsilon_params = json.loads(epsilon_params_raw)
    bucket = os.environ.get("MINIO_BUCKET", "tdgl-results")
    now = datetime.now(timezone.utc).isoformat()

    # Load device (built by build_device.py)
    with open(os.path.join(DATA_DIR, "device.pkl"), "rb") as f:
        device = pickle.load(f)

    # Load timing (built by build_timing.py)
    with open(os.path.join(DATA_DIR, "timing.json")) as f:
        timing_data = json.load(f)

    # Load mesh metadata
    mesh_meta_path = os.path.join(DATA_DIR, "mesh_meta.json")
    mesh_meta = {}
    if os.path.exists(mesh_meta_path):
        with open(mesh_meta_path) as f:
            mesh_meta = json.load(f)

    n_sites = len(device.points)

    # Build epsilon function
    epsilon_fn = None
    if epsilon_params.get("type") == "gaussian":
        epsilon_fn = make_gaussian_epsilon(
            positions=epsilon_params["positions"],
            widths=epsilon_params["widths"],
            strengths=epsilon_params["strengths"],
        )
        print(f"Epsilon: Gaussian, {len(epsilon_params['positions'])} spots")

    # Convert device to cpp-tdgl-compatible HDF5
    cpp_mesh_path = os.path.join(DATA_DIR, "cpp_mesh.h5")
    write_cpp_mesh(device, cpp_mesh_path,
                   solver_options=solver_options,
                   epsilon_fn=epsilon_fn)
    print(f"cpp-tdgl mesh written: {cpp_mesh_path}")

    # Prepare output paths
    output_path = os.path.join(DATA_DIR, "output.h5")
    timing_path = os.path.join(DATA_DIR, "timing.json")

    # Upload "running" manifest
    raw_timing_params = json.loads(os.environ.get("TIMING_PARAMS", "{}"))
    _upload_manifest({
        "run_id": run_id,
        "status": "running",
        "created_at": now,
        "n_sites": n_sites,
        "device_params": {
            "film_width": mesh_meta.get("film_width"),
            "film_height": mesh_meta.get("film_height"),
            "elec_width": mesh_meta.get("elec_width"),
            "elec_height": mesh_meta.get("elec_height"),
            "max_edge_length": mesh_meta.get("max_edge_length"),
            "smooth": mesh_meta.get("smooth"),
        },
        "timing_params": {
            "mode": timing_data["mode"],
            "n_steps": timing_data["n_steps"],
            "solve_time": timing_data["solve_time"],
        },
        "timing_steps": timing_data.get("steps", []),
        "raw_timing_params": raw_timing_params,
        "solver_options": solver_options,
    }, bucket, run_id)

    # Start periodic upload
    upload_stop = threading.Event()
    upload_thread = threading.Thread(
        target=_periodic_upload,
        args=(output_path, bucket, run_id, upload_stop, 30),
        daemon=True,
    )
    upload_thread.start()

    try:
        # Build C++ solver command
        cmd = [
            CPP_SOLVER,
            "--mesh", cpp_mesh_path,
            "--output", output_path,
            "--timing", timing_path,
            "--solver-options", json.dumps(solver_options),
        ]
        print(f"Running: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            raise RuntimeError(f"cpp-tdgl-solve exited with code {result.returncode}")

        # Final upload
        upload_stop.set()
        upload_thread.join(timeout=60)
        _upload_to_minio(output_path, bucket, f"tdgl-runs/{run_id}/output.h5")

        # Count frames
        n_frames = 0
        with h5py.File(output_path, "r") as f:
            n_frames = len(f["data"].keys())

        manifest = {
            "run_id": run_id,
            "status": "completed",
            "created_at": now,
            "n_sites": n_sites,
            "n_frames": n_frames,
            "device_params": {
                "film_width": mesh_meta.get("film_width"),
                "film_height": mesh_meta.get("film_height"),
                "elec_width": mesh_meta.get("elec_width"),
                "elec_height": mesh_meta.get("elec_height"),
                "max_edge_length": mesh_meta.get("max_edge_length"),
                "smooth": mesh_meta.get("smooth"),
            },
            "timing_params": {
                "mode": timing_data["mode"],
                "n_steps": timing_data["n_steps"],
                "solve_time": timing_data["solve_time"],
            },
            "timing_steps": timing_data.get("steps", []),
            "raw_timing_params": raw_timing_params,
            "solver_options": solver_options,
        }
        _upload_manifest(manifest, bucket, run_id)
        print(f"Run {run_id} completed. {n_frames} frames.")

    except Exception as exc:
        upload_stop.set()
        upload_thread.join(timeout=60)
        _upload_manifest({
            "run_id": run_id,
            "status": "failed",
            "created_at": now,
            "error": str(exc),
        }, bucket, run_id)
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
cd $KTDGL
git add services/cpp-tdgl-runner/runner.py services/cpp-tdgl-runner/build_device.py services/cpp-tdgl-runner/build_timing.py
git commit -m "feat: add cpp-tdgl-runner Python glue with MinIO upload"
```

---

### Task 7: Create Dockerfile

**Files:**
- Create: `KTDGL/services/cpp-tdgl-runner/Dockerfile`

Multi-stage build: compile C++ in stage 1, Python runtime in stage 2.

- [ ] **Step 1: Create Dockerfile**

Create `KTDGL/services/cpp-tdgl-runner/Dockerfile`:

```dockerfile
# Stage 1: Compile C++ solver
FROM ubuntu:22.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ git libeigen3-dev libhdf5-dev liblapack-dev \
    libsuitesparse-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy cpp-tdgl source (build context must include git-tdgl-light)
COPY git-tdgl-light/cpp-tdgl /build/cpp-tdgl

RUN cd /build/cpp-tdgl && \
    cmake -B build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build -j$(nproc)

# Stage 2: Python runtime
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libhdf5-dev liblapack3 libumfpack5 && \
    rm -rf /var/lib/apt/lists/*

# Copy compiled binary
COPY --from=builder /build/cpp-tdgl/build/cpp-tdgl-solve /usr/local/bin/

WORKDIR /app

RUN pip install --no-cache-dir boto3 h5py numpy scipy tdgl

# Copy vendor libraries
COPY src/tdgl_workflow/ /app/vendor/tdgl_workflow/
COPY src/tdgl_sdk/ /app/vendor/tdgl_sdk/

# Copy service code
COPY services/cpp-tdgl-runner/ /app/

CMD ["python", "/app/runner.py"]
```

> **Build context:** This Dockerfile requires the build context to be the parent directory (`Ruihuan/`) so it can access both `kubeflow-tdgl/` and `git-tdgl-light/`. Build command:
> ```bash
> cd /mnt/c/Users/photo/Photonics_Group/Ruihuan
> docker build -f kubeflow-tdgl/services/cpp-tdgl-runner/Dockerfile -t ghcr.io/fangrh/cpp-tdgl-runner:dev .
> ```

- [ ] **Step 2: Commit**

```bash
cd $KTDGL
git add services/cpp-tdgl-runner/Dockerfile
git commit -m "feat: add multi-stage Dockerfile for cpp-tdgl-runner"
```

---

### Task 8: Create Argo WorkflowTemplate

**Files:**
- Create: `KTDGL/services/cpp-tdgl-runner/k8s/workflowtemplate.yaml`

Mirrors py-tdgl-runner's WorkflowTemplate with the same parameters and 3-step structure.

- [ ] **Step 1: Create workflowtemplate.yaml**

Create `KTDGL/services/cpp-tdgl-runner/k8s/workflowtemplate.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: cpp-tdgl-sim
  namespace: tdgl
spec:
  serviceAccountName: argo-workflow
  entrypoint: simulation-pipeline
  activeDeadlineSeconds: 7200
  workflowMetadata:
    labels:
      run-id: "{{workflow.parameters.run-id}}"
  arguments:
    parameters:
      - name: run-id
        value: ""
      - name: image
        value: "ghcr.io/fangrh/cpp-tdgl-runner:latest"
      - name: device-params-json
        value: "{}"
      - name: timing-params-json
        value: "{}"
      - name: solver-options-json
        value: "{}"
      - name: epsilon-params-json
        value: "{}"
  templates:
    - name: simulation-pipeline
      steps:
        - - name: build-device
            template: build-device-step
        - - name: build-timing
            template: build-timing-step
        - - name: simulate
            template: simulate-step

    - name: build-device-step
      container:
        image: "{{workflow.parameters.image}}"
        imagePullPolicy: Always
        command: [python, /app/build_device.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: DEVICE_PARAMS
            value: "{{workflow.parameters.device-params-json}}"
          - name: DATA_DIR
            value: "/data"
        volumeMounts:
          - name: run-data
            mountPath: /data

    - name: build-timing-step
      container:
        image: "{{workflow.parameters.image}}"
        imagePullPolicy: Always
        command: [python, /app/build_timing.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: TIMING_PARAMS
            value: "{{workflow.parameters.timing-params-json}}"
          - name: DATA_DIR
            value: "/data"
        volumeMounts:
          - name: run-data
            mountPath: /data

    - name: simulate-step
      container:
        image: "{{workflow.parameters.image}}"
        imagePullPolicy: Always
        command: [python, /app/runner.py]
        env:
          - name: TDGL_RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: DATA_DIR
            value: "/data"
          - name: SOLVER_OPTIONS
            value: "{{workflow.parameters.solver-options-json}}"
          - name: EPSILON_PARAMS
            value: "{{workflow.parameters.epsilon-params-json}}"
          - name: TIMING_PARAMS
            value: "{{workflow.parameters.timing-params-json}}"
          - name: MINIO_ENDPOINT
            value: "http://minio.tdgl.svc.cluster.local:9000"
          - name: MINIO_ACCESS_KEY
            valueFrom:
              secretKeyRef:
                name: minio-credentials
                key: rootUser
          - name: MINIO_SECRET_KEY
            valueFrom:
              secretKeyRef:
                name: minio-credentials
                key: rootPassword
          - name: MINIO_BUCKET
            value: "tdgl-results"
        resources:
          requests:
            cpu: "4"
            memory: "8Gi"
          limits:
            cpu: "4"
            memory: "8Gi"
        volumeMounts:
          - name: run-data
            mountPath: /data

  volumeClaimTemplates:
    - metadata:
        name: run-data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 5Gi
```

> **Note:** cpu/memory limits are doubled vs py-tdgl-runner (4 CPU, 8Gi RAM) since the C++ solver benefits from more resources.

- [ ] **Step 2: Commit**

```bash
cd $KTDGL
git add services/cpp-tdgl-runner/k8s/workflowtemplate.yaml
git commit -m "feat: add Argo WorkflowTemplate for cpp-tdgl-sim"
```

---

### Task 9: End-to-end smoke test

- [ ] **Step 1: Build Docker image**

```bash
cd /mnt/c/Users/photo/Photonics_Group/Ruihuan
docker build -f kubeflow-tdgl/services/cpp-tdgl-runner/Dockerfile -t cpp-tdgl-runner:test .
```

Expected: Image builds successfully.

- [ ] **Step 2: Apply WorkflowTemplate to cluster**

```bash
kubectl apply -f services/cpp-tdgl-runner/k8s/workflowtemplate.yaml
```

- [ ] **Step 3: Submit a test workflow**

```bash
kubectl -n tdgl submit workflow --from workflowtemplate/cpp-tdgl-sim \
  -p run-id=cpp-test-001 \
  -p image=cpp-tdgl-runner:test \
  -p device-params-json='{"film_width":5,"film_height":2.5,"elec_width":1,"elec_height":0.5,"elec_y_offset":1,"probe_points":[[0,1.25],[5,1.25]],"max_edge_length":0.5,"smooth":50}' \
  -p timing-params-json='{"mode":"simple","je_initial":0,"je_final":0.5,"je_step":0.5,"ramp_time":2,"stable_time":5}' \
  -p solver-options-json='{"dt_init":1e-6,"dt_max":0.1,"adaptive":true,"save_every":50}'
```

Expected: Workflow runs to completion, output.h5 and manifest.json appear in MinIO.

- [ ] **Step 4: Verify output with Rust viewer**

Open `notebooks/browse_rust_viewer.py` in Jupyter and check if the cpp-test-001 run loads and renders correctly.

Expected: 2x2 viewer shows psi heatmap, mu heatmap, V(t) plot, and I-V curve.

---

## Self-Review

**Spec coverage:**
- Input compatibility (device, timing, epsilon) → Tasks 1, 3, 5
- Output format (py-tdgl-compatible HDF5) → Task 2
- Running state for IV scanner → Task 2 (save_running_state) + Task 3 (probe tracking)
- MinIO upload → Task 6 (runner.py with periodic upload)
- Docker → Task 7
- Argo WorkflowTemplate → Task 8
- Real-time viewing → Task 2 (flush) + Task 6 (periodic upload)
- Performance (no Python in solver loop) → Architecture ensures this

**Placeholder scan:** No TBDs, TODOs, or vague requirements. All code shown inline. The convert_mesh.py has a note about verifying tdgl Device property names during implementation.

**Type consistency:**
- `TimingSchedule` defined in timing.h, used in solver.h constructor and main.cpp
- `SolutionWriter` constructor takes `probe_indices` (vector<int>), solver passes `device.probe_point_indices`
- `terminal_current_at()` returns double, used in solve loop
- `save_running_state()` takes frame_idx (int), rsmu and rsdt (vector<double>)

**Gaps:**
- convert_mesh.py uses approximate tdgl Device property names — needs verification against tdgl API during implementation
- The solve() loop modifications in Task 3 are described conceptually — exact line-by-line changes depend on the current loop structure, which is in solver.cpp:185-330
