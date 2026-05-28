# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file LICENSE.rst or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION ${CMAKE_VERSION}) # this file comes with cmake

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-src")
  file(MAKE_DIRECTORY "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-src")
endif()
file(MAKE_DIRECTORY
  "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-build"
  "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-subbuild/highfive-populate-prefix"
  "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-subbuild/highfive-populate-prefix/tmp"
  "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-subbuild/highfive-populate-prefix/src/highfive-populate-stamp"
  "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-subbuild/highfive-populate-prefix/src"
  "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-subbuild/highfive-populate-prefix/src/highfive-populate-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-subbuild/highfive-populate-prefix/src/highfive-populate-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/cpp-tdgl/build/_deps/highfive-subbuild/highfive-populate-prefix/src/highfive-populate-stamp${cfgdir}") # cfgdir has leading slash
endif()
