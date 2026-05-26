enable_testing()

# List test sources explicitly (don't use GLOB since it runs at configure time)
add_executable(test_operators test_operators.cpp)
target_link_libraries(test_operators PRIVATE tdgl_core GTest::gtest_main)
add_test(NAME test_operators COMMAND test_operators)

add_executable(test_mesh test_mesh.cpp)
target_link_libraries(test_mesh PRIVATE tdgl_core GTest::gtest_main)
add_test(NAME test_mesh COMMAND test_mesh)

add_executable(test_timing test_timing.cpp)
target_link_libraries(test_timing PRIVATE tdgl_core)
add_test(NAME test_timing COMMAND test_timing)
