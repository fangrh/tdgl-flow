cmake_minimum_required(VERSION 3.20)
project(cpp-tdgl VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(BUILD_TESTING "Build tests" ON)
option(BUILD_BENCHMARKS "Build benchmarks" OFF)

find_package(Eigen3 3.4 REQUIRED NO_MODULE)
find_package(HDF5 REQUIRED COMPONENTS C CXX)
find_package(OpenMP)
find_package(CURL REQUIRED)

# SuiteSparse: find UMFPACK
find_path(SUITESPARSE_INCLUDE_DIRS
    NAMES umfpack.h
    PATHS /usr/include /usr/include/suitesparse
)
find_library(UMFPACK_LIBRARY umfpack)
find_library(AMD_LIBRARY amd)
find_library(CHOLMOD_LIBRARY cholmod)
find_library(COLAMD_LIBRARY colamd)

if(NOT UMFPACK_LIBRARY OR NOT SUITESPARSE_INCLUDE_DIRS)
    message(FATAL_ERROR "SuiteSparse (UMFPACK) not found. Install: sudo apt install libsuitesparse-dev")
endif()

message(STATUS "Eigen3: ${EIGEN3_INCLUDE_DIR}")
message(STATUS "HDF5: ${HDF5_INCLUDE_DIRS}")
message(STATUS "SuiteSparse: ${SUITESPARSE_INCLUDE_DIRS}")
message(STATUS "UMFPACK: ${UMFPACK_LIBRARY}")
message(STATUS "SuiteSparse: ${SUITESPARSE_INCLUDE_DIRS}")
message(STATUS "UMFPACK: ${UMFPACK_LIBRARY}")
# HighFive via FetchContent
include(FetchContent)
FetchContent_Declare(
    highfive
    GIT_REPOSITORY https://github.com/BlueBrain/HighFive.git
    GIT_TAG v2.10.0
)
set(HIGHFIVE_USE_BOOST OFF CACHE BOOL "" FORCE)
set(HIGHFIVE_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(HIGHFIVE_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(HIGHFIVE_INSTALL OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(highfive)

# nlohmann/json via FetchContent
FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
)
FetchContent_MakeAvailable(nlohmann_json)

# Common library (shared between executable, tests, benchmarks)
add_library(tdgl_core
    src/mesh/edge_mesh.cpp
    src/mesh/mesh.cpp
    src/mesh/io.cpp
    src/device/layer.cpp
    src/device/device.cpp
    src/options/options.cpp
    src/operators/operators.cpp
    src/operators/poisson_solver.cpp
    src/solver/psi_update.cpp
    src/solver/adaptive_step.cpp
    src/solver/observables.cpp
    src/solver/solver.cpp
    src/solver/screening.cpp
    src/solution/solution.cpp
    src/timing/timing.cpp
)
target_include_directories(tdgl_core PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${SUITESPARSE_INCLUDE_DIRS}
)
target_link_libraries(tdgl_core PUBLIC
    Eigen3::Eigen
    ${UMFPACK_LIBRARY}
    ${AMD_LIBRARY}
    ${CHOLMOD_LIBRARY}
    ${COLAMD_LIBRARY}
    HighFive
    HDF5::HDF5
    nlohmann_json::nlohmann_json
)
if(OpenMP_CXX_FOUND)
    target_link_libraries(tdgl_core PUBLIC OpenMP::OpenMP_CXX)
endif()

# Main executable
add_executable(tdgl_solve src/main.cpp src/sync/minio_synchronizer.cpp)
target_link_libraries(tdgl_solve PRIVATE tdgl_core ${CURL_LIBRARIES})
target_include_directories(tdgl_solve PRIVATE ${CURL_INCLUDE_DIRS})

# Tests
if(BUILD_TESTING)
    enable_testing()
    FetchContent_Declare(
        googletest
        GIT_REPOSITORY https://github.com/google/googletest.git
        GIT_TAG v1.14.0
    )
    set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(googletest)

    add_subdirectory(tests)
endif()

# Benchmarks
if(BUILD_BENCHMARKS)
    FetchContent_Declare(
        benchmark
        GIT_REPOSITORY https://github.com/google/benchmark.git
        GIT_TAG v1.8.3
    )
    set(BENCHMARK_ENABLE_TESTING OFF CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(benchmark)

    add_subdirectory(benchmarks)
endif()
