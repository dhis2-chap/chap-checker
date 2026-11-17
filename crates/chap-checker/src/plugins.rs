//! Plugins: what chap-checker knows how to check.
//!
//! chap-checker is an uptime monitor at its core: the runner, the state file and alerts on
//! transitions, `verify`, `serve` and the TUI. Each kind of server it understands is a plugin,
//! compiled in. A plugin owns its `kind = "..."` value, its config fields (in [`crate::config`]),
//! its checks (in [`crate::checks`], each listing the kinds it applies to), and how its tile reads:
//! a platform label, the check that counts as its ping, and where its version comes from.
//!
//! Adding a plugin: a [`TargetKind`] variant, an entry here, its checks, and its config fields.

use crate::http::TargetKind;

/// One kind of server chap-checker can check.
#[derive(Debug)]
pub struct Plugin {
    pub kind: TargetKind,
    /// The platform label on a tile ("DHIS2").
    pub label: &'static str,
    pub description: &'static str,
    /// The check whose results feed a tile's ping counter and uptime.
    pub ping_check: &'static str,
    /// The check and the `details` key a tile's version label comes from.
    pub version: Option<(&'static str, &'static str)>,
    /// A copy-paste `[instances.<key>]` table for `chap-checker plugins list`.
    pub toml_example: &'static str,
}

pub static PLUGINS: [Plugin; 5] = [
    Plugin {
        kind: TargetKind::Dhis2,
        label: "DHIS2",
        description: "A DHIS2 instance, and the chap-core integration reached through its `chap` route.",
        ping_check: "dhis2_ping",
        version: Some(("dhis2_system_info", "version")),
        toml_example: r#"[instances.prod]
url = "https://dhis2.example.org"     # kind = "dhis2" is the default
username = "admin"                    # Basic auth: username + password / password_env
password_env = "PROD_DHIS2_PASSWORD"
# token_env = "PROD_DHIS2_TOKEN"      # or a Personal Access Token, instead of a password
"#,
    },
    Plugin {
        kind: TargetKind::Ogs,
        label: "OGS",
        description: "An Open Geo Service (openEO backend) deployment.",
        ping_check: "ogs_health",
        version: Some(("ogs_info", "version")),
        toml_example: r#"[instances.nepal-ogs]
kind = "ogs"
url = "https://ogs.example.org"
"#,
    },
    Plugin {
        kind: TargetKind::Chap,
        label: "Chap Core",
        description: "A chap-core server reached directly (its /health and /system/info).",
        ping_check: "chap_health",
        version: Some(("chap_system_info", "chap_core_version")),
        toml_example: r#"[instances.chap-stable]
kind = "chap"
url = "https://example.org/chap/stable"   # chap-core's base URL, without /health
"#,
    },
    Plugin {
        kind: TargetKind::Caps,
        label: "CAPS",
        description: "A CAPS server (Climate Automation & Prediction Scheduler): its version, and whether it reaches chap-core and DHIS2.",
        ping_check: "caps_system_info",
        version: Some(("caps_system_info", "version")),
        toml_example: r#"[instances.caps]
kind = "caps"
url = "https://example.org/caps"          # CAPS's base URL, without /system/info
"#,
    },
    Plugin {
        kind: TargetKind::Http,
        label: "HTTP",
        description: "Any URL: answers with an expected status, and optionally a body or JSON field.",
        ping_check: "http_expect",
        version: None,
        toml_example: r#"[instances.landing]
kind = "http"
url = "https://example.org/"
# expect_status = [200]                  # default: any 2xx (redirects are followed, up to 5)
# body_contains = "Welcome"              # the body must contain this text
# json_path = "status"                   # a dotted path into a JSON body ...
# json_equals = "success"                # ... and the value it must hold
# headers = { Accept = "application/json" }
"#,
    },
];

/// The plugin for `kind`.
pub fn plugin(kind: TargetKind) -> &'static Plugin {
    PLUGINS
        .iter()
        .find(|p| p.kind == kind)
        .expect("every kind has a plugin")
}

/// The `kind` values, quoted and comma-separated, for messages.
pub fn kind_list() -> String {
    PLUGINS
        .iter()
        .map(|p| format!("'{}'", p.kind.as_str()))
        .collect::<Vec<_>>()
        .join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::checks;

    #[test]
    fn every_kind_has_one_plugin_whose_checks_exist() {
        for kind in TargetKind::ALL {
            let p = plugin(kind);
            let owned: Vec<&str> = checks::all_checks()
                .into_iter()
                .filter(|c| c.applies_to(kind))
                .map(|c| c.name)
                .collect();
            assert!(!owned.is_empty(), "{kind:?} has no checks");
            assert!(
                owned.contains(&p.ping_check),
                "{kind:?} ping {}",
                p.ping_check
            );
            if let Some((check, _)) = p.version {
                assert!(owned.contains(&check), "{kind:?} version {check}");
            }
        }
        assert_eq!(PLUGINS.len(), TargetKind::ALL.len());
    }
}
