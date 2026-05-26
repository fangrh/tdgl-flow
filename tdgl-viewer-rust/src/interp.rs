use rayon::prelude::*;

const K_NEIGHBORS: usize = 8;

/// Pre-computed IDW interpolation weights for mapping unstructured mesh
/// values to a regular grid. Computed once per run, then reused for every frame.
pub struct InterpolationGrid {
    /// For each grid point (NX*NY), indices of K nearest mesh sites.
    indices: Vec<[usize; K_NEIGHBORS]>,
    /// For each grid point, IDW weights (sum to ~1.0).
    weights: Vec<[f64; K_NEIGHBORS]>,
}

impl InterpolationGrid {
    pub fn new(sites: &[[f64; 2]], nx: usize, ny: usize) -> Self {
        let n_pts = sites.len();
        let grid_size = nx * ny;

        if n_pts == 0 || grid_size == 0 {
            return InterpolationGrid {
                indices: vec![[0; K_NEIGHBORS]; grid_size],
                weights: vec![[0.0; K_NEIGHBORS]; grid_size],
            };
        }

        let x_min = sites.iter().map(|p| p[0]).fold(f64::MAX, f64::min);
        let x_max = sites.iter().map(|p| p[0]).fold(f64::MIN, f64::max);
        let y_min = sites.iter().map(|p| p[1]).fold(f64::MAX, f64::min);
        let y_max = sites.iter().map(|p| p[1]).fold(f64::MIN, f64::max);

        let dx = if nx > 1 {
            (x_max - x_min) / (nx - 1) as f64
        } else {
            0.0
        };
        let dy = if ny > 1 {
            (y_max - y_min) / (ny - 1) as f64
        } else {
            0.0
        };

        let results: Vec<([usize; K_NEIGHBORS], [f64; K_NEIGHBORS])> = (0..grid_size)
            .into_par_iter()
            .map(|gi| {
                let gx = gi % nx;
                let gy = gi / nx;
                let x = x_min + gx as f64 * dx;
                let y = y_min + gy as f64 * dy;

                // Compute squared distances to all mesh sites
                let mut dists: Vec<(f64, usize)> = sites
                    .iter()
                    .enumerate()
                    .map(|(i, p)| {
                        let ddx = p[0] - x;
                        let ddy = p[1] - y;
                        (ddx * ddx + ddy * ddy, i)
                    })
                    .collect();

                // Sort to find K nearest
                dists.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));

                let k = K_NEIGHBORS.min(dists.len());
                let mut indices = [0usize; K_NEIGHBORS];
                let mut weights = [0.0f64; K_NEIGHBORS];

                // Exact match: single neighbor dominates
                if k > 0 && dists[0].0 < 1e-20 {
                    indices[0] = dists[0].1;
                    weights[0] = 1.0;
                    return (indices, weights);
                }

                // IDW with power p=2: weight = 1/d^2 = 1/d_sq
                let mut w_sum = 0.0;
                for j in 0..k {
                    let w = 1.0 / dists[j].0;
                    indices[j] = dists[j].1;
                    weights[j] = w;
                    w_sum += w;
                }

                if w_sum > 0.0 {
                    for w in &mut weights[..k] {
                        *w /= w_sum;
                    }
                }

                (indices, weights)
            })
            .collect();

        let mut all_indices = vec![[0usize; K_NEIGHBORS]; grid_size];
        let mut all_weights = vec![[0.0f64; K_NEIGHBORS]; grid_size];
        for (i, (idx, w)) in results.into_iter().enumerate() {
            all_indices[i] = idx;
            all_weights[i] = w;
        }

        InterpolationGrid {
            indices: all_indices,
            weights: all_weights,
        }
    }

    /// Interpolate mesh values to the regular grid.
    pub fn interpolate(&self, values: &[f64]) -> Vec<f64> {
        self.indices
            .iter()
            .zip(self.weights.iter())
            .map(|(idx, w)| {
                let mut v = 0.0;
                for j in 0..K_NEIGHBORS {
                    if idx[j] < values.len() {
                        v += w[j] * values[idx[j]];
                    }
                }
                v
            })
            .collect()
    }
}
