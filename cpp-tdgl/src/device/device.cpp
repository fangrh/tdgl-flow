#include "device/device.h"
#include "mesh/io.h"
#include <highfive/H5File.hpp>
#include <highfive/H5Group.hpp>
#include <highfive/H5Attribute.hpp>
#include <highfive/H5DataSet.hpp>
#include <iostream>
#include <vector>

namespace h5 = HighFive;

template<typename T>
std::vector<T> read_1d(const h5::Group& g, const std::string& name) {
    auto ds = g.getDataSet(name);
    auto dims = ds.getDimensions();
    size_t n = dims[0];
    for (size_t i = 1; i < dims.size(); ++i) n *= dims[i];
    std::vector<T> buf(n);
    ds.read_raw(buf.data());
    return buf;
}

Layer read_layer(const h5::Group& grp) {
    Layer layer;
    layer.london_lambda = grp.getAttribute("london_lambda").read<double>();
    layer.coherence_length = grp.getAttribute("coherence_length").read<double>();
    layer.thickness = grp.getAttribute("thickness").read<double>();
    layer.u = grp.getAttribute("u").read<double>();
    layer.gamma = grp.getAttribute("gamma").read<double>();
    if (grp.hasAttribute("z0"))
        layer.z0 = grp.getAttribute("z0").read<double>();
    if (grp.hasAttribute("conductivity"))
        layer.conductivity = grp.getAttribute("conductivity").read<double>();
    return layer;
}

TerminalInfo read_terminal_info(const h5::Group& grp) {
    TerminalInfo ti;
    auto si = read_1d<int64_t>(grp, "site_indices");
    ti.site_indices.resize(si.size());
    for (size_t i = 0; i < si.size(); ++i) ti.site_indices(i) = static_cast<int>(si[i]);
    auto ei = read_1d<int64_t>(grp, "edge_indices");
    ti.edge_indices.resize(ei.size());
    for (size_t i = 0; i < ei.size(); ++i) ti.edge_indices(i) = static_cast<int>(ei[i]);
    auto bei = read_1d<int64_t>(grp, "boundary_edge_indices");
    ti.boundary_edge_indices.resize(bei.size());
    for (size_t i = 0; i < bei.size(); ++i) ti.boundary_edge_indices(i) = static_cast<int>(bei[i]);
    ti.length = grp.getAttribute("length").read<double>();
    return ti;
}

Device read_device(const std::string& h5_path) {
    h5::File file(h5_path, h5::File::ReadOnly);
    Device device;
    auto dg = file.getGroup("device");

    device.name = dg.getAttribute("name").read<std::string>();
    device.length_units = dg.getAttribute("length_units").read<std::string>();
    device.layer = read_layer(dg.getGroup("layer"));
    device.mesh = read_mesh(file);

    if (dg.exist("terminals")) {
        auto tg = dg.getGroup("terminals");
        for (const auto& name : tg.listObjectNames()) {
            auto ti = read_terminal_info(tg.getGroup(name));
            ti.name = name;
            device.terminals.push_back(ti);
        }
    }

    if (dg.exist("probe_point_indices")) {
        auto ppi = read_1d<int64_t>(dg, "probe_point_indices");
        device.probe_point_indices.resize(ppi.size());
        for (size_t i = 0; i < ppi.size(); ++i)
            device.probe_point_indices[i] = static_cast<int>(ppi[i]);
    }

    if (dg.hasAttribute("K0"))
        device.K0 = dg.getAttribute("K0").read<double>();
    if (dg.hasAttribute("A0"))
        device.A0 = dg.getAttribute("A0").read<double>();
    if (dg.hasAttribute("Bc2"))
        device.Bc2 = dg.getAttribute("Bc2").read<double>();
    if (dg.hasAttribute("Lambda"))
        device.Lambda = dg.getAttribute("Lambda").read<double>();

    return device;
}

Options read_options(const std::string& h5_path) {
    h5::File file(h5_path, h5::File::ReadOnly);
    return read_options(file);
}

Options read_options(h5::File& file) {
    Options opts;
    h5::Group og;
    if (file.exist("options"))
        og = file.getGroup("options");
    else if (file.exist("solution/options"))
        og = file.getGroup("solution/options");
    else
        return opts;

    auto ra = [&](const char* name, auto& val) {
        if (og.hasAttribute(name)) og.getAttribute(name).read(val);
    };
    ra("solve_time", opts.solve_time);
    ra("skip_time", opts.skip_time);
    ra("dt_init", opts.dt_init);
    ra("dt_max", opts.dt_max);
    ra("adaptive", opts.adaptive);
    ra("adaptive_window", opts.adaptive_window);
    ra("max_solve_retries", opts.max_solve_retries);
    ra("adaptive_time_step_multiplier", opts.adaptive_time_step_multiplier);
    ra("terminal_psi", opts.terminal_psi);
    ra("save_every", opts.save_every);
    ra("include_screening", opts.include_screening);
    ra("max_iterations_per_step", opts.max_iterations_per_step);
    ra("screening_tolerance", opts.screening_tolerance);
    ra("screening_step_size", opts.screening_step_size);
    ra("screening_step_drag", opts.screening_step_drag);
    ra("applied_field", opts.applied_field);
    if (og.hasAttribute("field_units"))
        opts.field_units = og.getAttribute("field_units").read<std::string>();
    if (og.hasAttribute("current_units"))
        opts.current_units = og.getAttribute("current_units").read<std::string>();

    return opts;
}
