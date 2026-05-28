use crate::colormaps;

const FRAME_W: u32 = 760;
const FRAME_H: u32 = 470;
const PANEL_W: u32 = 360;
const PANEL_H: u32 = 180;
const NX: usize = 100;
const NY: usize = 50;
const PSI_VMAX: f64 = 1.05;
const TOP_Y: u32 = 36;
const BOTTOM_Y: u32 = TOP_Y + PANEL_H + 16; // 232

const HMAP_W: u32 = 342;
const CBAR_OFFSET: u32 = HMAP_W + 4;
const CBAR_W: u32 = 8;

const CHAR_W: u32 = 5;
const CHAR_H: u32 = 7;

#[derive(Clone, Copy)]
pub struct PlotRegion {
    pub x0: f64,
    pub x1: f64,
    pub color: [u8; 4],
}

pub fn render_frame_2x2(
    psi_grid: &[f64],
    mu_grid: &[f64],
    mu_vmax: f64,
    frame_idx: usize,
    total_frames: usize,
    vt_data: Option<&[(f64, f64)]>,
    vt_regions: Option<&[PlotRegion]>,
    highlight_vt: Option<(f64, f64)>,
    iv_data: Option<&[(f64, f64)]>,
    highlight_iv: Option<(f64, f64)>,
    step_info: Option<&str>,
) -> Vec<u8> {
    let mut canvas = vec![30u8; (FRAME_W * FRAME_H * 4) as usize];

    // Panel titles
    draw_text_centered(
        &mut canvas,
        14,
        14 + PANEL_W,
        2,
        "|ψ|²",
        [220, 220, 230, 255],
        2,
    );
    draw_text_centered(
        &mut canvas,
        386,
        386 + PANEL_W,
        2,
        "μ",
        [220, 220, 230, 255],
        2,
    );
    draw_text_centered(
        &mut canvas,
        14,
        14 + PANEL_W,
        BOTTOM_Y - 12,
        "V(t)",
        [190, 190, 200, 255],
        1,
    );
    draw_text_centered(
        &mut canvas,
        386,
        386 + PANEL_W,
        BOTTOM_Y - 12,
        "I-V",
        [190, 190, 200, 255],
        1,
    );

    // Frame counter top-right
    let frame_str = format!("frame {} / {}", frame_idx, total_frames.saturating_sub(1));
    let fw = text_width(&frame_str, 1);
    draw_text(
        &mut canvas,
        (FRAME_W - fw - 8) as i32,
        6,
        &frame_str,
        [140, 140, 150, 255],
        1,
    );

    // Step info below frame counter
    if let Some(info) = step_info {
        let iw = text_width(info, 1);
        draw_text(
            &mut canvas,
            (FRAME_W - iw - 8) as i32,
            16,
            info,
            [160, 160, 80, 255],
            1,
        );
    }

    // Top-left: |ψ|² heatmap + colorbar
    let psi_norm: Vec<f64> = psi_grid
        .iter()
        .map(|&v| (v / PSI_VMAX).clamp(0.0, 1.0))
        .collect();
    let psi_rgba = apply_colormap(&psi_norm, &colormaps::INFERNO);
    blit_panel(&mut canvas, &psi_rgba, 14, TOP_Y, NX, NY, HMAP_W, PANEL_H);
    draw_colorbar(
        &mut canvas,
        (14 + CBAR_OFFSET) as i32,
        TOP_Y + 8,
        CBAR_W,
        PANEL_H - 16,
        &colormaps::INFERNO,
        0.0,
        PSI_VMAX,
    );

    // Top-right: μ heatmap + colorbar
    let mu_norm: Vec<f64> = mu_grid
        .iter()
        .map(|&v| ((v + mu_vmax) / (2.0 * mu_vmax)).clamp(0.0, 1.0))
        .collect();
    let mu_rgba = apply_colormap(&mu_norm, &colormaps::RDBU_R);
    blit_panel(&mut canvas, &mu_rgba, 386, TOP_Y, NX, NY, HMAP_W, PANEL_H);
    draw_colorbar(
        &mut canvas,
        (386 + CBAR_OFFSET) as i32,
        TOP_Y + 8,
        CBAR_W,
        PANEL_H - 16,
        &colormaps::RDBU_R,
        -mu_vmax,
        mu_vmax,
    );

    // Bottom-left: V vs time (with current frame position highlight)
    draw_plot(
        &mut canvas,
        14,
        BOTTOM_Y,
        PANEL_W,
        PANEL_H,
        vt_data.unwrap_or(&[]),
        [255, 100, 80, 255],
        vt_regions.unwrap_or(&[]),
        highlight_vt,
        [255, 255, 80, 255],
        "t",
        "V",
        false,
    );

    // Bottom-right: I-V curve
    draw_plot(
        &mut canvas,
        386,
        BOTTOM_Y,
        PANEL_W,
        PANEL_H,
        iv_data.unwrap_or(&[]),
        [80, 180, 255, 255],
        &[],
        highlight_iv,
        [255, 255, 80, 255],
        "I",
        "V",
        true,
    );

    encode_png(&canvas, FRAME_W, FRAME_H)
}

pub fn apply_colormap(values: &[f64], lut: &[[u8; 4]; 256]) -> Vec<u8> {
    values
        .iter()
        .flat_map(|&v| {
            let idx = ((v * 255.0).round() as usize).clamp(0, 255);
            lut[idx]
        })
        .collect()
}

fn blit_panel(
    canvas: &mut [u8],
    rgba: &[u8],
    x0: u32,
    y0: u32,
    src_w: usize,
    src_h: usize,
    dst_w: u32,
    dst_h: u32,
) {
    for dy in 0..dst_h {
        let sy = (dy as usize * src_h / dst_h as usize).min(src_h - 1);
        for dx in 0..dst_w {
            let sx = (dx as usize * src_w / dst_w as usize).min(src_w - 1);
            let src_idx = (sy * src_w + sx) * 4;
            let dst_idx = ((y0 + dy) * FRAME_W + x0 + dx) as usize * 4;
            if dst_idx + 4 <= canvas.len() && src_idx + 4 <= rgba.len() {
                canvas[dst_idx..dst_idx + 4].copy_from_slice(&rgba[src_idx..src_idx + 4]);
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

// ── Colorbar ────────────────────────────────────────────────────────

fn draw_colorbar(
    canvas: &mut [u8],
    x: i32,
    y: u32,
    w: u32,
    h: u32,
    lut: &[[u8; 4]; 256],
    vmin: f64,
    vmax: f64,
) {
    let xu = x as u32;

    // Border
    fill_rect(
        canvas,
        xu.saturating_sub(1),
        y.saturating_sub(1),
        w + 2,
        h + 2,
        [50, 50, 60, 255],
    );

    for dy in 0..h {
        let frac = 1.0 - dy as f64 / (h.saturating_sub(1)).max(1) as f64;
        let idx = (frac * 255.0).round() as usize;
        let color = lut[idx.min(255)];
        for dx in 0..w {
            set_pixel(canvas, xu + dx, y + dy, color);
        }
    }

    // Min / max labels to the right of the colorbar
    let lx = xu + w + 3;
    draw_text(
        canvas,
        lx as i32,
        y.saturating_sub(1),
        &format_tick(vmax),
        [170, 170, 180, 255],
        1,
    );
    draw_text(
        canvas,
        lx as i32,
        y + h - 7,
        &format_tick(vmin),
        [170, 170, 180, 255],
        1,
    );

    // Mid label
    if h > 40 {
        let mid_val = (vmin + vmax) / 2.0;
        draw_text(
            canvas,
            lx as i32,
            y + h / 2 - 3,
            &format_tick(mid_val),
            [130, 130, 140, 255],
            1,
        );
    }
}

// ── Line plot drawing ──────────────────────────────────────────────

const PLOT_ML: u32 = 46;
const PLOT_MR: u32 = 5;
const PLOT_MT: u32 = 5;
const PLOT_MB: u32 = 22;

fn draw_plot(
    canvas: &mut [u8],
    px: u32,
    py: u32,
    pw: u32,
    ph: u32,
    data: &[(f64, f64)],
    color: [u8; 4],
    regions: &[PlotRegion],
    highlight: Option<(f64, f64)>,
    highlight_color: [u8; 4],
    x_label: &str,
    y_label: &str,
    draw_points: bool,
) {
    let plot_x = px + PLOT_ML;
    let plot_y = py + PLOT_MT;
    let plot_w = pw - PLOT_ML - PLOT_MR;
    let plot_h = ph - PLOT_MT - PLOT_MB;

    // Panel background
    fill_rect(canvas, px, py, pw, ph, [20, 22, 30, 255]);
    // Plot area background
    fill_rect(canvas, plot_x, plot_y, plot_w, plot_h, [8, 8, 16, 255]);

    if data.len() < 2 {
        draw_plot_guides(canvas, plot_x, plot_y, plot_w, plot_h);
        // Axis labels even when empty
        draw_axis_labels(canvas, px, py, pw, ph, x_label, y_label);
        return;
    }

    let mut x_min = data.iter().map(|d| d.0).fold(f64::MAX, f64::min);
    let mut x_max = data.iter().map(|d| d.0).fold(f64::MIN, f64::max);
    // Include region boundaries so they're always visible even when data doesn't cover them
    for region in regions {
        x_min = x_min.min(region.x0).min(region.x1);
        x_max = x_max.max(region.x0).max(region.x1);
    }
    let y_min = data.iter().map(|d| d.1).fold(f64::MAX, f64::min);
    let y_max = data.iter().map(|d| d.1).fold(f64::MIN, f64::max);

    let x_span = if (x_max - x_min).abs() < 1e-15 {
        1.0
    } else {
        (x_max - x_min) * 1.1
    };
    let y_span = if (y_max - y_min).abs() < 1e-15 {
        1.0
    } else {
        (y_max - y_min) * 1.1
    };
    let x_center = (x_min + x_max) / 2.0;
    let y_center = (y_min + y_max) / 2.0;

    let map_x = |x: f64| -> i32 {
        plot_x as i32
            + (((x - x_center + x_span / 2.0) / x_span * (plot_w - 1) as f64) as i32)
                .clamp(0, plot_w as i32 - 1)
    };
    let map_y = |y: f64| -> i32 {
        (plot_y as i32 + plot_h as i32 - 1)
            - (((y - y_center + y_span / 2.0) / y_span * (plot_h - 1) as f64) as i32)
                .clamp(0, plot_h as i32 - 1)
    };

    // Background regions, used by V(t) to separate ramp/stable/averaging windows.
    for region in regions {
        let rx0 = map_x(region.x0.min(region.x1)).max(plot_x as i32) as u32;
        let rx1 = map_x(region.x0.max(region.x1)).min((plot_x + plot_w - 1) as i32) as u32;
        if rx1 >= rx0 {
            fill_rect(canvas, rx0, plot_y, rx1 - rx0 + 1, plot_h, region.color);
        }
    }

    draw_plot_guides(canvas, plot_x, plot_y, plot_w, plot_h);

    // Tick values along axes
    let x_lo = x_center - x_span / 2.0;
    let x_hi = x_center + x_span / 2.0;
    let y_lo = y_center - y_span / 2.0;
    let y_hi = y_center + y_span / 2.0;

    // Y-axis ticks (left side)
    let y_ticks = [y_hi, (y_hi + y_lo) / 2.0, y_lo];
    let y_positions = [plot_y + 2, plot_y + plot_h / 2 - 3, plot_y + plot_h - 8];
    for (i, (_, &ty)) in y_ticks.iter().zip(y_positions.iter()).enumerate() {
        let label = format_tick(y_ticks[i]);
        let tw = text_width(&label, 1);
        draw_text(
            canvas,
            (px + PLOT_ML - tw - 4) as i32,
            ty,
            &label,
            [130, 130, 140, 255],
            1,
        );
    }

    // X-axis ticks (bottom)
    let x_ticks = [x_lo, (x_lo + x_hi) / 2.0, x_hi];
    let x_offsets = [0, plot_w / 2, plot_w - 1];
    for (i, _) in x_ticks.iter().enumerate() {
        let label = format_tick(x_ticks[i]);
        let tw = text_width(&label, 1);
        let tx = plot_x as i32 + x_offsets[i] as i32 - tw as i32 / 2;
        draw_text(
            canvas,
            tx.max(px as i32),
            py + ph - PLOT_MB + 8,
            &label,
            [130, 130, 140, 255],
            1,
        );
    }

    // Axis labels
    draw_axis_labels(canvas, px, py, pw, ph, x_label, y_label);

    // Draw connecting lines
    for i in 1..data.len() {
        let x0 = map_x(data[i - 1].0);
        let y0 = map_y(data[i - 1].1);
        let x1 = map_x(data[i].0);
        let y1 = map_y(data[i].1);
        draw_line(canvas, x0 as u32, y0 as u32, x1 as u32, y1 as u32, color);
    }

    if draw_points {
        for &(x, y) in data {
            let px = map_x(x);
            let py = map_y(y);
            fill_circle(canvas, px as u32, py as u32, 2, color);
        }
    }

    // Highlight point
    if let Some((hx, hy)) = highlight {
        let hpx = map_x(hx);
        let hpy = map_y(hy);
        fill_circle(canvas, hpx as u32, hpy as u32, 5, highlight_color);
    }
}

fn draw_plot_guides(canvas: &mut [u8], plot_x: u32, plot_y: u32, plot_w: u32, plot_h: u32) {
    // Grid lines
    for i in 1..4 {
        let gy = plot_y + plot_h * i / 4;
        draw_line(
            canvas,
            plot_x,
            gy,
            plot_x + plot_w - 1,
            gy,
            [35, 35, 45, 255],
        );
        let gx = plot_x + plot_w * i / 4;
        draw_line(
            canvas,
            gx,
            plot_y,
            gx,
            plot_y + plot_h - 1,
            [35, 35, 45, 255],
        );
    }

    // Axes
    draw_line(
        canvas,
        plot_x,
        plot_y + plot_h - 1,
        plot_x + plot_w - 1,
        plot_y + plot_h - 1,
        [80, 80, 90, 255],
    );
    draw_line(
        canvas,
        plot_x,
        plot_y,
        plot_x,
        plot_y + plot_h - 1,
        [80, 80, 90, 255],
    );
}

fn draw_axis_labels(
    canvas: &mut [u8],
    px: u32,
    py: u32,
    pw: u32,
    _ph: u32,
    x_label: &str,
    y_label: &str,
) {
    let plot_x = px + PLOT_ML;
    let plot_w = pw - PLOT_ML - PLOT_MR;

    // X-axis label centered below ticks
    let tw = text_width(x_label, 1);
    draw_text(
        canvas,
        (plot_x + plot_w / 2 - tw / 2) as i32,
        py + PLOT_MT + (PANEL_H - PLOT_MT - PLOT_MB) + 14,
        x_label,
        [170, 170, 180, 255],
        1,
    );

    // Y-axis label to the left, centered vertically
    draw_text(
        canvas,
        px as i32 + 2,
        py + PLOT_MT + (PANEL_H - PLOT_MT - PLOT_MB) / 2 - 3,
        y_label,
        [170, 170, 180, 255],
        1,
    );
}

// ── Text drawing ────────────────────────────────────────────────────

fn glyph(c: char) -> [u8; 7] {
    match c {
        ' ' => [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        '.' => [0x00, 0x00, 0x00, 0x00, 0x00, 0x06, 0x06],
        '-' => [0x00, 0x00, 0x00, 0x0E, 0x00, 0x00, 0x00],
        '+' => [0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00],
        '=' => [0x00, 0x00, 0x1F, 0x00, 0x1F, 0x00, 0x00],
        '(' => [0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02],
        ')' => [0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08],
        ':' => [0x00, 0x06, 0x06, 0x00, 0x06, 0x06, 0x00],
        '/' => [0x01, 0x01, 0x02, 0x04, 0x08, 0x10, 0x10],
        '^' => [0x04, 0x0A, 0x11, 0x00, 0x00, 0x00, 0x00],
        '|' => [0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
        '_' => [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F],

        '0' => [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
        '1' => [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
        '2' => [0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F],
        '3' => [0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E],
        '4' => [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
        '5' => [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
        '6' => [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
        '7' => [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
        '8' => [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
        '9' => [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],

        'A' => [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'B' => [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
        'C' => [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
        'D' => [0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C],
        'E' => [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
        'F' => [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
        'G' => [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
        'H' => [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'I' => [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
        'J' => [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
        'K' => [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
        'L' => [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
        'M' => [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
        'N' => [0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11],
        'O' => [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'P' => [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
        'Q' => [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
        'R' => [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
        'S' => [0x0E, 0x11, 0x10, 0x0E, 0x01, 0x11, 0x0E],
        'T' => [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
        'U' => [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'V' => [0x11, 0x11, 0x11, 0x11, 0x0A, 0x0A, 0x04],
        'W' => [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
        'X' => [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
        'Y' => [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
        'Z' => [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],

        'a' => [0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F],
        'b' => [0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x1E],
        'c' => [0x00, 0x00, 0x0E, 0x11, 0x10, 0x11, 0x0E],
        'd' => [0x01, 0x01, 0x0F, 0x11, 0x11, 0x11, 0x0F],
        'e' => [0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E],
        'f' => [0x06, 0x08, 0x08, 0x1E, 0x08, 0x08, 0x08],
        'g' => [0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E],
        'h' => [0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x11],
        'i' => [0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E],
        'j' => [0x02, 0x00, 0x06, 0x02, 0x02, 0x12, 0x0C],
        'k' => [0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12],
        'l' => [0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
        'm' => [0x00, 0x00, 0x1A, 0x15, 0x15, 0x15, 0x11],
        'n' => [0x00, 0x00, 0x1E, 0x11, 0x11, 0x11, 0x11],
        'o' => [0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E],
        'p' => [0x00, 0x00, 0x1E, 0x11, 0x11, 0x1E, 0x10],
        'q' => [0x00, 0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01],
        'r' => [0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10],
        's' => [0x00, 0x00, 0x0F, 0x10, 0x0E, 0x01, 0x1E],
        't' => [0x08, 0x08, 0x1E, 0x08, 0x08, 0x09, 0x06],
        'u' => [0x00, 0x00, 0x11, 0x11, 0x11, 0x13, 0x0D],
        'v' => [0x00, 0x00, 0x11, 0x11, 0x0A, 0x0A, 0x04],
        'w' => [0x00, 0x00, 0x11, 0x11, 0x15, 0x1B, 0x11],
        'x' => [0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11],
        'y' => [0x00, 0x00, 0x11, 0x11, 0x0F, 0x01, 0x0E],
        'z' => [0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F],

        // Greek / special
        '\u{03C8}' => [0x15, 0x15, 0x15, 0x0E, 0x04, 0x04, 0x04], // ψ
        '\u{03BC}' => [0x00, 0x00, 0x12, 0x12, 0x12, 0x16, 0x19], // μ
        '\u{00B2}' => [0x00, 0x00, 0x06, 0x01, 0x02, 0x06, 0x00], // ²

        _ => [0; 7],
    }
}

fn text_width(text: &str, scale: u32) -> u32 {
    let n = text.chars().count() as u32;
    if n == 0 {
        return 0;
    }
    n * (CHAR_W + 1) * scale - 1 * scale
}

fn draw_text(canvas: &mut [u8], x: i32, y: u32, text: &str, color: [u8; 4], scale: u32) {
    let mut cx = x;
    for c in text.chars() {
        let g = glyph(c);
        for (row, &bits) in g.iter().enumerate() {
            for col in 0..5u32 {
                if bits & (0x10 >> col) != 0 {
                    for sy in 0..scale {
                        for sx in 0..scale {
                            let px = cx + col as i32 * scale as i32 + sx as i32;
                            let py = y + row as u32 * scale + sy;
                            if px >= 0 && (px as u32) < FRAME_W && py < FRAME_H {
                                set_pixel(canvas, px as u32, py, color);
                            }
                        }
                    }
                }
            }
        }
        cx += (CHAR_W + 1) as i32 * scale as i32;
    }
}

fn draw_text_centered(
    canvas: &mut [u8],
    left: u32,
    right: u32,
    y: u32,
    text: &str,
    color: [u8; 4],
    scale: u32,
) {
    let tw = text_width(text, scale);
    let center = (left + right) / 2;
    let x = center as i32 - tw as i32 / 2;
    draw_text(canvas, x, y, text, color, scale);
}

fn format_tick(v: f64) -> String {
    if v.abs() < 1e-10 {
        return "0".to_string();
    }
    let abs = v.abs();
    if abs >= 10000.0 || abs < 0.01 {
        format!("{:.0e}", v)
    } else if abs >= 100.0 {
        format!("{:.0}", v)
    } else if abs >= 10.0 {
        format!("{:.1}", v)
    } else {
        format!("{:.2}", v)
    }
}

// ── Primitive drawing ──────────────────────────────────────────────

fn set_pixel(canvas: &mut [u8], x: u32, y: u32, color: [u8; 4]) {
    let idx = ((y * FRAME_W + x) as usize) * 4;
    if idx + 4 <= canvas.len() {
        canvas[idx..idx + 4].copy_from_slice(&color);
    }
}

fn fill_rect(canvas: &mut [u8], x: u32, y: u32, w: u32, h: u32, color: [u8; 4]) {
    for row in 0..h {
        let start = ((y + row) * FRAME_W + x) as usize * 4;
        let end = start + w as usize * 4;
        if end <= canvas.len() {
            let mut i = start;
            while i < end {
                canvas[i..i + 4].copy_from_slice(&color);
                i += 4;
            }
        }
    }
}

fn fill_circle(canvas: &mut [u8], cx: u32, cy: u32, r: u32, color: [u8; 4]) {
    let r_sq = (r * r) as i32;
    for dy in -(r as i32)..=(r as i32) {
        for dx in -(r as i32)..=(r as i32) {
            if dx * dx + dy * dy <= r_sq {
                let px = (cx as i32 + dx) as u32;
                let py = (cy as i32 + dy) as u32;
                set_pixel(canvas, px, py, color);
            }
        }
    }
}

/// Bresenham line drawing.
fn draw_line(canvas: &mut [u8], x0: u32, y0: u32, x1: u32, y1: u32, color: [u8; 4]) {
    let mut cx = x0 as i32;
    let mut cy = y0 as i32;
    let dx = (x1 as i32 - cx).abs();
    let dy = -(y1 as i32 - cy).abs();
    let sx: i32 = if cx < x1 as i32 { 1 } else { -1 };
    let sy: i32 = if cy < y1 as i32 { 1 } else { -1 };
    let mut err = dx + dy;

    loop {
        set_pixel(canvas, cx as u32, cy as u32, color);
        if cx == x1 as i32 && cy == y1 as i32 {
            break;
        }
        let e2 = 2 * err;
        if e2 >= dy {
            err += dy;
            cx += sx;
        }
        if e2 <= dx {
            err += dx;
            cy += sy;
        }
    }
}
