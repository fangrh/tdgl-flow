use crate::colormaps;

const FRAME_W: u32 = 760;
const FRAME_H: u32 = 470;
const PANEL_W: u32 = 360;
const PANEL_H: u32 = 180;
const NX: usize = 100;
const NY: usize = 50;
const PSI_VMAX: f64 = 1.05;

pub fn render_frame_2x2(
    psi_abs: &[f64],   // |psi| values at mesh sites (N,)
    mu_raw: &[f64],    // mu values at mesh sites (N,)
    mu_vmax: f64,
    frame_idx: usize,
    total_frames: usize,
) -> Vec<u8> {
    let mut canvas = vec![30u8; (FRAME_W * FRAME_H * 4) as usize]; // dark gray BG

    // Psi panel: normalize |psi| to [0, 1] using PSI_VMAX
    let psi_norm: Vec<f64> = psi_abs.iter().map(|&v| (v / PSI_VMAX).clamp(0.0, 1.0)).collect();
    let psi_rgba = apply_colormap(&psi_norm, &colormaps::INFERNO);
    blit_panel(&mut canvas, &psi_rgba, 14, 42, NX, NY, PANEL_W, PANEL_H);

    // Mu panel: normalize to [0, 1] using ±mu_vmax
    let mu_norm: Vec<f64> = mu_raw.iter()
        .map(|&v| ((v + mu_vmax) / (2.0 * mu_vmax)).clamp(0.0, 1.0))
        .collect();
    let mu_rgba = apply_colormap(&mu_norm, &colormaps::RDBU_R);
    blit_panel(&mut canvas, &mu_rgba, 386, 42, NX, NY, PANEL_W, PANEL_H);

    // Bottom panels are for V-vs-t and I-V, which will be added later
    // For now, leave them as dark gray

    encode_png(&canvas, FRAME_W, FRAME_H)
}

pub fn apply_colormap(values: &[f64], lut: &[[u8; 4]; 256]) -> Vec<u8> {
    values.iter().flat_map(|&v| {
        let idx = ((v * 255.0).round() as usize).clamp(0, 255);
        lut[idx]
    }).collect()
}

fn blit_panel(canvas: &mut [u8], rgba: &[u8], x0: u32, y0: u32,
              src_w: usize, src_h: usize, dst_w: u32, dst_h: u32) {
    for dy in 0..dst_h {
        let sy = (dy as usize * src_h / dst_h as usize).min(src_h - 1);
        for dx in 0..dst_w {
            let sx = (dx as usize * src_w / dst_w as usize).min(src_w - 1);
            let src_idx = (sy * src_w + sx) * 4;
            let dst_idx = ((y0 + dy) * FRAME_W + x0 + dx) as usize * 4;
            if dst_idx + 4 <= canvas.len() && src_idx + 4 <= rgba.len() {
                canvas[dst_idx..dst_idx+4].copy_from_slice(&rgba[src_idx..src_idx+4]);
            }
        }
    }
}

fn encode_png(rgba: &[u8], w: u32, h: u32) -> Vec<u8> {
    let img = image::RgbaImage::from_raw(w, h, rgba.to_vec()).unwrap();
    let mut buf = std::io::Cursor::new(Vec::new());
    img.write_to(&mut buf, image::ImageFormat::Png).unwrap();
    buf.into_inner()
}