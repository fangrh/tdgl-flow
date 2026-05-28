# Spec: cuda-tdgl — CUDA-accelerated TDGL Solver

## Objective

Create `cuda-tdgl`, a GPU-accelerated implementation of the Time-Dependent Ginzburg-Landau (TDGL) solver using CUDA. The algorithm and numerical methods are identical to the existing `cpp-tdgl` C++ implementation — this is a performance port, not a redesign.

**Target users**: Researchers in the Photonics Group running large-scale simulations (50k–500k sites) on consumer NVIDIA GPUs (RTX 3060/4060 tier, ~8–12GB VRAM).

**Success criteria**:
- Results match cpp-tdgl within numerical tolerance (|psi|^2 relative error < 1e-6, mu relative error < 1e-6)
- Wall-clock speedup of at least 5x over cpp-tdgl on large meshes (50k+ sites) for the full solve loop
- Speedup of at least 50x for the Biot-Savart screening kernel (O(n^2) is the main GPU target)
- HDF5 output fully compatible with py-tdgl visualization tools
- Fits in ~8GB VRAM for meshes up to 200k sites

## Governing Equations (unchanged from cpp-tdgl)

All equations are identical to cpp-tdgl. This spec documents only the GPU implementation strategy.

### Key Computational Kernels

| Kernel | FLOPs pattern | GPU strategy | Expected speedup |
|--------|--------------|--------------|-----------------|
| Psi update (nonlinear solve) | Element-wise + SpMV | cuSPARSE SpMV + element-wise kernels | 5-20x |
| Supercurrent J_s | SpMV + element-wise | cuSPARSE SpMV + kernel | 5-10x |
| Poisson solve (mu) | Sparse LU solve | cuSOLVER cusolverSp | 2-5x (or keep CPU) |
| Biot-Savart screening | O(ne * ns) dense pairs | 2D GPU kernel grid | 50-200x |
| Edge-to-site interpolation | Scatter/add | Atomic add kernel | 10-30x |
| Link variable update | Element-wise | Simple kernel | 5-10x |
| Adaptive step logic | Control flow | Stays on CPU | N/A |

### Memory Layout (GPU)

For consumer GPUs with limited VRAM, data stays on GPU across time steps. Only initial upload and final download incur transfers.

**Device memory (persistent across solve loop):**
- `psi` (complex double): num_sites * 16 bytes
- `mu` (double): num_sites * 8 bytes
- `epsilon` (double): num_sites * 8 bytes
- `psi_laplacian` CSR (complex double): ~2 * num_edges * 24 bytes
- `psi_gradient` CSR (complex double): ~2 * num_edges * 24 bytes
- `mu_gradient` CSR (double): ~2 * num_edges * 16 bytes
- `divergence` CSR (double): ~2 * num_edges * 16 bytes
- `mu_laplacian` CSR (double): ~2 * num_edges * 16 bytes
- `mu_boundary_laplacian` CSR (double): ~2 * num_boundary_edges * 16 bytes
- Edge indices, weights, directions: ~num_edges * 40 bytes
- Screening buffers: A_induced, J_site, velocity (num_edges * 16 bytes each)

**Estimated total for 100k sites / 200k edges**: ~150-200MB (fits easily in 8GB)

**For 500k sites / 1M edges**: ~750MB-1.5GB (fits in 8GB)

### Biot-Savart Kernel Strategy

The O(ne * ns) Biot-Savart kernel is the primary GPU target. For large meshes:
- Grid: (ne, 1, 1) blocks, each block processes a subset of ns sites
- Use shared memory for edge center coordinates (reused across sites)
- Tile the site loop to avoid register pressure
- For very large ne * ns (> VRAM capacity for temporaries), process edge batches

```
For 100k sites, 200k edges: 100k * 200k = 2e10 operations
GPU throughput ~10 TFLOP/s -> ~2 seconds (vs ~minutes on CPU)
```

### Sparse Matrix Updates for Screening

During screening iterations, link variables change and the complex sparse matrices must be rebuilt. Strategy:
1. Store sparse matrix *structure* (row_ptr, col_ind) persistently on GPU
2. Store *base values* (real Laplacian weights) persistently on GPU
3. Only update the *values* array when link variables change (kernel: multiply base weights by link variables)
4. This avoids rebuilding row_ptr/col_ind every screening iteration

## Tech Stack

| Component | Choice | Version |
|-----------|--------|---------|
| Language | C++17 with CUDA | - |
| CUDA Toolkit | 11.8+ (SM_75+ for Ampere) | 11.8+ |
| Sparse ops | cuSPARSE | 11.8+ |
| Sparse solver | cuSOLVER (cusolverSp) | 11.8+ |
| Dense BLAS | cuBLAS | 11.8+ |
| HDF5 I/O | HighFive (CPU-only, same as cpp-tdgl) | 2.7+ |
| Build system | CMake | 3.20+ |
| Testing | Google Test | 1.14+ |

## Commands

```bash
# Configure (requires CUDA toolkit)
cmake -B build -DCMAKE_BUILD_TYPE=Release \
      -DCUDA_ARCHITECTURES="75;86;89" \
      -DSUITESPARSE_ROOT=/path/to/suitesparse

# Build
cmake --build build -j$(nproc)

# Run solver
./build/bin/cuda_tdgl_solve --mesh input.h5 --output output.h5 \
    --source-current 1.0 --drain-current -1.0

# Run tests
ctest --test-dir build --output-on-failure

# Run benchmarks
./build/bin/cuda_tdgl_benchmark

# Compare against cpp-tdgl reference
python scripts/compare.py --ref data/cpp_output.h5 --cuda data/cuda_output.h5
```

## Project Structure

```
cuda-tdgl/
├── CMakeLists.txt                  # Top-level CMake with CUDA enable
├── SPEC.md                         # This specification
├── src/
│   ├── CMakeLists.txt
│   ├── main.cpp                    # CLI entry point (CPU-side setup)
│   ├── cuda_common.h               # CUDA error checking, device selection
│   ├── gpu_allocator.h             # RAII device memory management
│   ├── mesh/
│   │   ├── mesh.h                  # Host-side mesh data structures (shared with cpp-tdgl)
│   │   ├── edge_mesh.h             # Host-side edge mesh (shared with cpp-tdgl)
│   │   └── io.h / io.cpp           # HDF5 I/O (CPU-only, identical to cpp-tdgl)
│   ├── device/
│   │   ├── device.h / device.cpp   # Device geometry (CPU-side, identical to cpp-tdgl)
│   │   ├── layer.h                 # Material properties (shared header)
│   │   └── terminal_info.h         # Terminal definitions (shared header)
│   ├── options/
│   │   └── options.h               # Solver configuration (shared header)
│   ├── gpu/
│   │   ├── gpu_mesh.cu / .h        # Copy mesh data to GPU, device-side mesh struct
│   │   ├── gpu_operators.cu / .h   # Build & store sparse operators on GPU
│   │   ├── gpu_psi_update.cu / .h  # Psi nonlinear solve kernel
│   │   ├── gpu_observables.cu / .h # Supercurrent, normal current kernels
│   │   ├── gpu_poisson.cu / .h     # Poisson solve via cuSOLVER
│   │   ├── gpu_screening.cu / .h   # Biot-Savart kernel + interpolation
│   │   └── gpu_solver.cu / .h      # Main GPU time loop orchestrator
│   └── solution/
│       ├── solution.h / solution.cpp  # HDF5 output (CPU-side, identical to cpp-tdgl)
│       └── gpu_solution.cu / .h       # Download GPU results to CPU for HDF5 write
├── tests/
│   ├── CMakeLists.txt
│   ├── test_mesh.cpp               # Mesh I/O tests (CPU, shared with cpp-tdgl)
│   ├── test_gpu_operators.cu       # GPU operator correctness vs CPU reference
│   ├── test_gpu_psi_update.cu      # GPU psi update vs CPU reference
│   ├── test_gpu_screening.cu       # GPU Biot-Savart vs CPU reference
│   └── test_gpu_solver.cu          # Full GPU solve regression vs cpp-tdgl output
├── benchmarks/
│   ├── CMakeLists.txt
│   └── bench_gpu_solver.cu         # GPU benchmarks with CUDA events timing
└── scripts/
    └── compare.py                  # Compare cuda-tdgl vs cpp-tdgl HDF5 outputs
```

### Code Sharing Strategy

The following modules are **identical** between cpp-tdgl and cuda-tdgl and can be copied or symlinked:
- `mesh/mesh.h`, `mesh/edge_mesh.h` — data structure definitions
- `mesh/io.h`, `mesh/io.cpp` — HDF5 mesh I/O
- `device/layer.h`, `device/terminal_info.h` — simple structs
- `device/device.cpp` — HDF5 device reading
- `options/options.h` — Options struct
- `solution/solution.h`, `solution/solution.cpp` — HDF5 output

The `gpu/` directory contains all CUDA-specific code. The main loop in `gpu_solver.cu` mirrors `solver/solver.cpp` but dispatches to GPU kernels instead of Eigen operations.

## Code Style

```cpp
// Naming: snake_case for functions/variables, PascalCase for classes
// CUDA suffix: _kernel for __global__ functions, _device for __device__ functions
// RAII for all GPU resources (device memory, streams, events)

// Example GPU kernel
__global__ void psi_update_kernel(
    const cuComplex* __restrict__ psi,
    const double* __restrict__ mu,
    const double* __restrict__ epsilon,
    const cuComplex* __restrict__ z,
    cuComplex* __restrict__ psi_out,
    double* __restrict__ abs_sq_out,
    double gamma_sq_half, double dt_over_u,
    int n
) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= n) return;

    cuComplex psi_i = psi[idx];
    double mu_i = mu[idx];
    double eps_i = epsilon[idx];
    double abs_sq = cuCabs(psi_i) * cuCabs(psi_i);
    // ... compute w, discriminant, psi_new ...
}

// RAII device memory wrapper
template<typename T>
struct DeviceBuffer {
    T* data = nullptr;
    size_t size = 0;

    DeviceBuffer() = default;
    explicit DeviceBuffer(size_t n) : size(n) {
        CUDA_CHECK(cudaMalloc(&data, n * sizeof(T)));
    }
    ~DeviceBuffer() { if (data) cudaFree(data); }
    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;
    DeviceBuffer(DeviceBuffer&& o) noexcept : data(o.data), size(o.size) {
        o.data = nullptr; o.size = 0;
    }
};
```

**Key conventions:**
- Double precision throughout (cuDoubleComplex / cuComplex is float — use cufftDoubleComplex / custom)
- Actually: use `double2` for complex double, `float2` for complex float
- Use `__restrict__` pointers in kernels to enable cache optimization
- Use CUDA streams for overlapping computation with data transfer
- Use CUDA events for accurate kernel timing in benchmarks
- All error checking via `CUDA_CHECK()` macro (prints file:line and aborts)
- No exceptions in GPU code; return error codes or use assertions

### Custom Complex Type

CUDA's `cuDoubleComplex` has poor operator support. We define:
```cpp
struct DoubleComplex {
    double x;  // real
    double y;  // imag
};
__device__ __host__ DoubleComplex make_dc(double r, double i);
__device__ __host__ DoubleComplex dc_mul(DoubleComplex a, DoubleComplex b);
__device__ __host__ DoubleComplex dc_conj(DoubleComplex a);
__device__ __host__ double dc_abs_sq(DoubleComplex a);
// etc.
```

## Testing Strategy

**Framework**: Google Test with CUDA test registration.

**Test levels:**

| Level | What | Location | Method |
|-------|------|----------|--------|
| Unit | Individual GPU kernels | `test_gpu_operators.cu`, `test_gpu_psi_update.cu` | Upload known data, run kernel, download, compare vs CPU reference |
| Integration | Full GPU solve on small mesh | `test_gpu_solver.cu` | Run GPU solve, compare output HDF5 vs cpp-tdgl reference |
| Regression | Large mesh accuracy | `test_gpu_solver.cu` | Compare |psi|^2 and mu against cpp-tdgl reference |

**Regression test approach:**
1. Run cpp-tdgl on reference case, save output as `data/reference_output.h5`
2. CUDA test loads same input, runs GPU solver, downloads results
3. Compare: max(|psi_gpu|^2 - |psi_cpu|^2) / max(|psi_cpu|^2) < 1e-6
4. Same for mu and current densities

**Accuracy notes:**
- GPU reductions (sum, max) may differ from CPU by ~1 ULP due to different reduction order
- cuSOLVER sparse LU may return different particular solution for singular systems (mu Laplacian), but physical observables (gradients, currents) should match
- Biot-Savart on GPU may use float64 accumulation with different summation order

**Performance benchmarks:**
- CUDA events for kernel timing (not wall-clock)
- Compare: cpp-tdgl CPU time vs cuda-tdgl GPU time for same problem
- Report: total solve time, per-kernel breakdown (psi update, observables, screening, data transfer)

## Boundaries

### Always
- Run CPU tests before committing (`ctest --test-dir build`)
- Use HDF5 for all I/O (same format as cpp-tdgl)
- Match cpp-tdgl numerical results within tolerance
- Use double precision for all solver computations
- Check CUDA errors after every API call via CUDA_CHECK macro
- Validate GPU compute capability at startup

### Ask first
- Switching from cuSOLVER sparse LU to iterative solver (e.g., AMGX)
- Adding multi-GPU support
- Changing the Biot-Savart kernel strategy (e.g., FMM approximation)
- Modifying shared header files that cpp-tdgl also uses

### Never
- Use single precision for core solver arithmetic
- Materialize the full O(ne*ns) Biot-Savart distance matrix in GPU memory
- Modify the cpp-tdgl codebase
- Commit reference data or generated HDF5 files
- Skip CUDA error checking for "performance"

## Implementation Phases

### Phase 1: Foundation
- Project scaffolding (CMake with CUDA, RAII wrappers, error macros)
- Copy shared headers from cpp-tdgl (mesh, device, options structs)
- GPU mesh upload (copy host mesh data to device)
- GPU sparse matrix builder (upload CSR structures + values)

### Phase 2: Core Kernels
- Psi update kernel (element-wise + SpMV via cuSPARSE)
- Supercurrent kernel (SpMV + element-wise complex multiply)
- Poisson solve (cuSOLVER sparse LU factorize + solve)
- Observables (normal current = -gradient*mu - dA/dt)

### Phase 3: Solver Loop
- GPU time loop (mirrors cpu solve loop)
- Link variable update kernel
- Adaptive time stepping (mostly CPU control logic)
- Screening: Biot-Savart kernel + interpolation + Polyak update
- Result download + HDF5 write

### Phase 4: Testing & Benchmarks
- Unit tests for each GPU kernel
- Full solve regression test
- Performance benchmarks vs cpp-tdgl

## Open Questions

1. **cuSOLVER sparse LU vs CPU UMFPACK**: cuSOLVER's sparse direct solver may be slower than CPU UMFPACK for the singular mu Laplacian. Should we provide a fallback to CPU solve for the Poisson equation and only GPU-accelerate the rest?
2. **Screening data batching**: For 500k sites, the Biot-Savart kernel processes 500k * 1M = 5e11 pairs. Should we batch edge processing to stay within VRAM, or rely on the kernel's O(1) memory footprint (it only needs one edge center + reduces over sites)?
3. **Shared headers**: Should shared headers be git submodules, copied, or symlinked from cpp-tdgl?
