//! The tideSI panel pill for the COSMIC desktop.
//!
//! Only the pill is native. The popup is the shared QML of `ui/`, shown by
//! the desktop tray app (`hosts/desktop/app.py`), which under COSMIC draws no
//! tray icon and instead writes what the pill shows to `cosmic-pill.json`.
//! This applet watches that file and, on a click, runs `tidesi` — a second
//! start hands over to the running one, which toggles its window.

use std::{
    env, fs,
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, SystemTime},
};

use cosmic::{
    Task, app,
    applet::cosmic_panel_config::PanelAnchor,
    iced::{self, Alignment, Color, Length},
    surface,
    widget::{self, Id, autosize},
};
use serde::Deserialize;

const APP_ID: &str = "org.muddyblack.tidesi.CosmicApplet";
const POLL: Duration = Duration::from_secs(2);
// The thresholds of every other tideSI panel (ui/PanelSlot.qml).
const WARNING: Color = Color::from_rgb(1.0, 0.651, 0.302);
const DANGER: Color = Color::from_rgb(1.0, 0.302, 0.302);

#[derive(Debug, Clone, Default, Deserialize)]
struct Slot {
    #[serde(default)]
    pct: f32,
    #[serde(default)]
    color: String,
    #[serde(default)]
    text: String,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct Pill {
    #[serde(default)]
    slots: Vec<Slot>,
    #[serde(default)]
    icon: String,
    #[serde(default)]
    tooltip: String,
}

/// Where the tray app publishes the pill (`COSMIC_STATE` in app.py): the
/// runtime directory, or tideSI's cache folder where there is none.
fn state_path() -> PathBuf {
    let dir = env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(|| {
        env::var_os("XDG_CACHE_HOME")
            .map(PathBuf::from)
            .or_else(|| env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache")))
            .unwrap_or_else(env::temp_dir)
            .join("tidesi")
    });
    dir.join("tidesi").join("cosmic-pill.json")
}

fn tidesi_command() -> String {
    env::var("TIDESI_COMMAND").unwrap_or_else(|_| "tidesi".into())
}

fn spawn_tidesi(args: &[&str]) {
    let cmd = tidesi_command();
    let mut parts = cmd.split_whitespace();
    let Some(program) = parts.next() else { return };
    if let Ok(mut child) = Command::new(program)
        .args(parts)
        .args(args)
        .stdin(Stdio::null())
        .spawn()
    {
        std::thread::spawn(move || {
            let _ = child.wait();
        });
    }
}

fn parse_color(hex: &str) -> Option<Color> {
    let hex = hex.strip_prefix('#')?;
    let v = u32::from_str_radix(hex.get(..6)?, 16).ok()?;
    Some(Color::from_rgb8((v >> 16) as u8, (v >> 8) as u8, v as u8))
}

struct Applet {
    core: app::Core,
    path: PathBuf,
    modified: Option<SystemTime>,
    pill: Pill,
}

#[derive(Debug, Clone)]
enum Msg {
    Tick,
    Press,
    Surface(surface::Action),
}

impl Applet {
    fn reload(&mut self) {
        let modified = fs::metadata(&self.path).and_then(|m| m.modified()).ok();
        if modified == self.modified {
            return;
        }
        self.modified = modified;
        self.pill = fs::read(&self.path)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default();
    }

    fn slot_view(&self, slot: &Slot) -> cosmic::Element<'_, Msg> {
        let brand = parse_color(&slot.color);
        let value = slot.pct.round() as i32;
        // The logo carries the provider's colour; the number stays neutral
        // until it is worth a look.
        let number_color = if value >= 90 {
            Some(DANGER)
        } else if value >= 70 {
            Some(WARNING)
        } else {
            None
        };
        let text = if slot.text.is_empty() {
            format!("{value}%")
        } else {
            slot.text.clone()
        };
        let size = self.core.applet.suggested_size(false).0 as f32;

        let mut label = widget::text(text).size(self.core.applet.suggested_size(false).1 as f32 * 0.7);
        if let Some(color) = number_color {
            label = label.class(cosmic::theme::Text::Color(color));
        }
        let logo = widget::icon(widget::icon::from_path(PathBuf::from(&self.pill.icon)))
            .width(Length::Fixed(size))
            .height(Length::Fixed(size))
            .class(match brand {
                Some(color) => cosmic::theme::Svg::Custom(std::rc::Rc::new(move |_| {
                    iced::widget::svg::Style { color: Some(color) }
                })),
                None => cosmic::theme::Svg::default(),
            });

        if self.vertical() {
            widget::column::with_children(vec![logo.into(), label.into()]).align_x(Alignment::Center).spacing(2).into()
        } else {
            widget::row::with_children(vec![logo.into(), label.into()]).align_y(Alignment::Center).spacing(4).into()
        }
    }

    fn vertical(&self) -> bool {
        matches!(self.core.applet.anchor, PanelAnchor::Left | PanelAnchor::Right)
    }
}

impl cosmic::Application for Applet {
    type Message = Msg;
    type Executor = cosmic::SingleThreadExecutor;
    type Flags = PathBuf;
    const APP_ID: &'static str = APP_ID;

    fn init(core: app::Core, path: PathBuf) -> (Self, app::Task<Msg>) {
        let mut applet = Self { core, path, modified: None, pill: Pill::default() };
        applet.reload();
        // Keep the data fresh without opening anything: the tray app does the
        // polling, so it must be running whether or not the popup is.
        spawn_tidesi(&["--cosmic", "--background"]);
        (applet, Task::none())
    }

    fn core(&self) -> &app::Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut app::Core {
        &mut self.core
    }

    fn style(&self) -> Option<iced::theme::Style> {
        Some(cosmic::applet::style())
    }

    fn update(&mut self, message: Msg) -> app::Task<Msg> {
        match message {
            Msg::Tick => self.reload(),
            // Not running yet: its first start opens the popup itself.
            Msg::Press if self.modified.is_none() => spawn_tidesi(&["--cosmic", "--open"]),
            Msg::Press => spawn_tidesi(&["--cosmic"]),
            Msg::Surface(a) => {
                return cosmic::task::message(cosmic::Action::Cosmic(app::Action::Surface(a)));
            }
        }
        Task::none()
    }

    fn view(&self) -> cosmic::Element<'_, Msg> {
        let (major, minor) = self.core.applet.suggested_padding(false);
        let (h_pad, v_pad) = if self.vertical() { (minor, major) } else { (major, minor) };

        let content: cosmic::Element<'_, Msg> = if self.pill.slots.is_empty() {
            let size = self.core.applet.suggested_size(false).0 as f32;
            widget::icon(widget::icon::from_name("org.muddyblack.tidesi").handle())
                .width(Length::Fixed(size))
                .height(Length::Fixed(size))
                .into()
        } else if self.vertical() {
            widget::column::with_children(self.pill.slots.iter().map(|s| self.slot_view(s)))
                .align_x(Alignment::Center)
                .spacing(6)
                .into()
        } else {
            widget::row::with_children(self.pill.slots.iter().map(|s| self.slot_view(s)))
                .align_y(Alignment::Center)
                .spacing(10)
                .into()
        };

        let button = widget::button::custom(content)
            .padding([v_pad, h_pad])
            .class(cosmic::theme::Button::AppletIcon)
            .on_press_down(Msg::Press);

        let tip = if self.pill.tooltip.is_empty() { "tideSI".to_owned() } else { self.pill.tooltip.clone() };
        autosize::autosize(
            self.core.applet.applet_tooltip::<Msg>(button, tip, false, Msg::Surface, None),
            Id::new("tidesi-autosize"),
        )
        .into()
    }

    fn subscription(&self) -> iced::Subscription<Msg> {
        iced::time::every(POLL).map(|_| Msg::Tick)
    }
}

fn main() -> cosmic::iced::Result {
    cosmic::applet::run::<Applet>(state_path())
}
