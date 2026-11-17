//! Render a verify report: one table per target on a terminal, or one JSON document for cron.

use std::io::IsTerminal;

use comfy_table::presets::UTF8_FULL_CONDENSED;
use comfy_table::{
    Attribute, Cell, CellAlignment, Color, ColumnConstraint, ContentArrangement, Table, Width,
};

use crate::checks::Status;
use crate::runner::VerifyReport;

/// Print `report` to stdout.
pub fn render(report: &VerifyReport, json: bool) {
    if json {
        println!(
            "{}",
            crate::jsonfmt::to_pretty_sorted(report).expect("report serializes")
        );
        return;
    }
    let color = std::io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none();
    for run in &report.runs {
        let display = run
            .target_display_name
            .as_deref()
            .unwrap_or(&run.target_name);
        println!("chap-checker - {display} - {}", run.target_url);
        let mut table = new_table(color);
        table.set_header(["Check", "Status", "Duration", "Message"]);
        // Check, Status and Duration are fixed-size (content plus one cell of padding each side);
        // Message takes the rest and wraps.
        for (index, width) in [(0, 27), (1, 9), (2, 10)] {
            if let Some(column) = table.column_mut(index) {
                column.set_constraint(ColumnConstraint::Absolute(Width::Fixed(width)));
            }
        }
        if let Some(column) = table.column_mut(2) {
            column.set_cell_alignment(CellAlignment::Right);
        }
        for r in &run.results {
            table.add_row(vec![
                styled(Cell::new(&r.name), color, Some(Color::Cyan), false),
                status_cell(r.status, color),
                styled(
                    Cell::new(format!("{:.0} ms", r.duration_ms)),
                    color,
                    Some(Color::DarkGrey),
                    false,
                ),
                Cell::new(&r.message),
            ]);
        }
        println!("{table}");
    }
}

/// A table that fills the terminal width.
pub fn new_table(color: bool) -> Table {
    let mut table = Table::new();
    table
        .load_style(UTF8_FULL_CONDENSED)
        .set_content_arrangement(ContentArrangement::DynamicFullWidth);
    if !color {
        table.force_no_tty();
    }
    table
}

/// Apply a foreground color and optional bold when color is on.
pub fn styled(cell: Cell, color: bool, fg: Option<Color>, bold: bool) -> Cell {
    if !color {
        return cell;
    }
    let mut cell = cell;
    if let Some(fg) = fg {
        cell = cell.fg(fg);
    }
    if bold {
        cell = cell.add_attribute(Attribute::Bold);
    }
    cell
}

fn status_cell(status: Status, color: bool) -> Cell {
    let fg = match status {
        Status::Ok => Color::Green,
        Status::Warn => Color::Yellow,
        Status::Fail => Color::Red,
        Status::Error => Color::Magenta,
        Status::Skipped => Color::DarkGrey,
    };
    styled(
        Cell::new(status.as_str().to_uppercase()),
        color,
        Some(fg),
        status != Status::Skipped,
    )
}
