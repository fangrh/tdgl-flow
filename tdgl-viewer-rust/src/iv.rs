pub struct FrameIV {
    pub current: f64,
    pub voltage: f64,
    pub time: f64,
}

/// Compute time-weighted voltage from running_state arrays.
/// rsmu: (2*K,) flattened — first K entries are mu row 0, next K are mu row 1
/// rsdt: (K,) — dt values for time weighting
pub fn compute_frame_voltage(rsmu: &[f64], rsdt: &[f64]) -> f64 {
    let k = rsdt.len();
    if k == 0 || rsmu.len() < 2 * k {
        return f64::NAN;
    }
    let voltage_samples: Vec<f64> = (0..k)
        .map(|i| rsmu[i] - rsmu[k + i])
        .collect();
    let dt_sum: f64 = rsdt.iter().sum();
    if dt_sum > 0.0 {
        voltage_samples.iter()
            .zip(rsdt.iter())
            .map(|(v, dt)| v * dt)
            .sum::<f64>() / dt_sum
    } else {
        voltage_samples.iter().sum::<f64>() / k as f64
    }
}

/// Compute transport current from terminal currents array.
/// terminal_currents is typically a short array from the mesh.
/// For a simple rectangular device with two electrodes:
/// current = sum of terminal currents at one electrode
pub fn compute_current(terminal_currents: &[f64]) -> f64 {
    // Sum of positive currents (source electrode)
    terminal_currents.iter().filter(|&&c| c > 0.0).sum()
}