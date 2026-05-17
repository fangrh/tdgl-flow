#pragma once

#include "device/layer.h"
#include "device/terminal_info.h"
#include "options/options.h"
#include "mesh/mesh.h"
#include <highfive/H5File.hpp>
#include <highfive/H5Group.hpp>
#include <string>
#include <vector>

struct Device {
    std::string name;
    std::string length_units = "um";
    Layer layer;
    Mesh mesh;
    std::vector<TerminalInfo> terminals;
    std::vector<int> probe_point_indices;
    double K0 = 0.0;
    double A0 = 0.0;
    double Bc2 = 0.0;
    double Lambda = 0.0;
};

Layer read_layer(const HighFive::Group& grp);
TerminalInfo read_terminal_info(const HighFive::Group& grp);
Device read_device(const std::string& h5_path);
Options read_options(const std::string& h5_path);
Options read_options(HighFive::File& file);
