#pragma once

#include "mesh/mesh.h"
#include <highfive/H5File.hpp>
#include <string>

Mesh read_mesh(const std::string& h5_path);
Mesh read_mesh(HighFive::File& file);
void write_mesh(const Mesh& mesh, const std::string& h5_path);
void write_mesh(const Mesh& mesh, HighFive::File& file);
