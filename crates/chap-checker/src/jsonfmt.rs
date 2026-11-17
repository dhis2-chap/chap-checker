//! JSON text in the shape the Python implementation printed.
//!
//! `json.dump(..., indent=2, sort_keys=True)` with Python's defaults: keys sorted at every level,
//! struct fields included, two-space indent, and non-ASCII escaped as `\uXXXX`
//! (`ensure_ascii=True`). Scripts parsing `verify --json` see the same bytes as before.
//!
//! The keys are sorted here, explicitly: whether serde_json's map keeps insertion order depends
//! on a feature any crate in the build can turn on (sokkel-tui does).

use serde::Serialize;

/// Serialize `value` as pretty, key-sorted, ASCII-only JSON.
pub fn to_pretty_sorted<T: Serialize>(value: &T) -> serde_json::Result<String> {
    let value = sorted(serde_json::to_value(value)?);
    let text = serde_json::to_string_pretty(&value)?;
    Ok(escape_non_ascii(&text))
}

/// `value` with every object's keys in sorted order, at every depth.
fn sorted(value: serde_json::Value) -> serde_json::Value {
    use serde_json::Value;
    match value {
        Value::Object(map) => {
            let mut entries: Vec<(String, Value)> = map.into_iter().collect();
            entries.sort_by(|a, b| a.0.cmp(&b.0));
            Value::Object(entries.into_iter().map(|(k, v)| (k, sorted(v))).collect())
        }
        Value::Array(items) => Value::Array(items.into_iter().map(sorted).collect()),
        other => other,
    }
}

/// Replace every non-ASCII character with its `\uXXXX` escape (surrogate pairs above U+FFFF).
///
/// Safe on whole JSON documents: non-ASCII characters can only occur inside string literals.
pub fn escape_non_ascii(text: &str) -> String {
    if text.is_ascii() {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len() + 16);
    for ch in text.chars() {
        if ch.is_ascii() {
            out.push(ch);
            continue;
        }
        let mut units = [0u16; 2];
        for unit in ch.encode_utf16(&mut units) {
            out.push_str(&format!("\\u{unit:04x}"));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sorts_keys_and_escapes() {
        let text = to_pretty_sorted(&json!({"b": 1, "a": "\u{e6}\u{1f600}"})).unwrap();
        assert_eq!(
            text,
            "{\n  \"a\": \"\\u00e6\\ud83d\\ude00\",\n  \"b\": 1\n}"
        );
    }
}
