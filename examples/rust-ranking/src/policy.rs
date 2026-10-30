//! KITE ordering policy with the licensed Flea matcher, not oma-cast's JS ranker.
use crate::backend::{
    searchmatch::{Letter, Matcher, State, EXACT, HIT_SPAN, NAME, SHADOW_TYPO},
    searchtypo::Scratch,
};
use serde::Deserialize;
use std::collections::{HashMap, HashSet};
use unicode_normalization::{char::canonical_combining_class, UnicodeNormalization};

const SAFE: u64 = 1 << 53;
const HALF_LIFE: f64 = 864000.0;
const TERM_WINDOW: f64 = 1468800.0;

#[derive(Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Candidate {
    pub id: String,
    title: String,
    subtitle: String,
    keywords: String,
    kind: String,
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Usage {
    #[serde(default, deserialize_with = "present")]
    count: Option<u64>,
    #[serde(default, deserialize_with = "present")]
    last: Option<f64>,
    #[serde(default, deserialize_with = "present")]
    anchor: Option<f64>,
    #[serde(default)]
    terms: Vec<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Sensitivity {
    Low,
    Medium,
    High,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    protocol_version: u32,
    pub catalog_revision: u64,
    query: String,
    candidates: Vec<Candidate>,
    usage: HashMap<String, Usage>,
    aliases: HashMap<String, String>,
    favorites: Vec<String>,
    limit: usize,
    sensitivity: Sensitivity,
    learn_queries: bool,
}

fn present<'de, D: serde::Deserializer<'de>, T: Deserialize<'de>>(
    d: D,
) -> Result<Option<T>, D::Error> {
    T::deserialize(d).map(Some)
}

fn bounded(text: &str, maximum: usize) -> bool {
    text.chars().count() <= maximum
}

impl Params {
    pub fn split(self) -> Result<(Catalog, Query), &'static str> {
        if self.protocol_version != 1 {
            return Err("Unsupported legacy ranking version");
        }
        Ok((
            Catalog {
                protocol_version: 2,
                catalog_revision: self.catalog_revision,
                candidates: self.candidates,
                usage: self.usage,
                aliases: self.aliases,
                favorites: self.favorites,
            },
            Query {
                protocol_version: 2,
                catalog_revision: self.catalog_revision,
                query: self.query,
                limit: self.limit,
                sensitivity: self.sensitivity,
                learn_queries: self.learn_queries,
            },
        ))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    protocol_version: u32,
    pub catalog_revision: u64,
    candidates: Vec<Candidate>,
    usage: HashMap<String, Usage>,
    aliases: HashMap<String, String>,
    favorites: Vec<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Query {
    protocol_version: u32,
    pub catalog_revision: u64,
    query: String,
    limit: usize,
    sensitivity: Sensitivity,
    learn_queries: bool,
}

impl Query {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.protocol_version != 2
            || self.catalog_revision >= SAFE
            || !bounded(&self.query, 4096)
            || !(1..=200).contains(&self.limit)
        {
            return Err("Invalid ranking query");
        }
        Ok(())
    }
}

impl Catalog {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.protocol_version != 2
            || self.catalog_revision >= SAFE
            || self.candidates.len() > 10000
        {
            return Err("Invalid ranking request bounds or version");
        }
        let mut ids = HashSet::new();
        for row in &self.candidates {
            if row.id.is_empty()
                || !bounded(&row.id, 1024)
                || !ids.insert(row.id.as_str())
                || [&row.title, &row.subtitle, &row.keywords, &row.kind]
                    .iter()
                    .any(|s| !bounded(s, 8192))
            {
                return Err("Invalid or duplicate candidate");
            }
        }
        if self
            .aliases
            .iter()
            .any(|(id, alias)| !ids.contains(id.as_str()) || !bounded(alias, 1024))
            || self.usage.keys().any(|id| !ids.contains(id.as_str()))
            || self.favorites.iter().any(|id| !ids.contains(id.as_str()))
            || self.favorites.iter().collect::<HashSet<_>>().len() != self.favorites.len()
        {
            return Err("Invalid ranking context IDs");
        }
        for usage in self.usage.values() {
            if usage.count.is_some_and(|n| n >= SAFE)
                || [usage.last, usage.anchor]
                    .into_iter()
                    .flatten()
                    .any(|n| !n.is_finite() || n.abs() >= SAFE as f64)
                || usage.terms.len() > 20
                || usage.terms.iter().any(|s| !bounded(s, 512))
            {
                return Err("Invalid usage record");
            }
        }
        Ok(())
    }
}

pub fn transliterate(text: &str) -> String {
    let mut translated = String::with_capacity(text.len());
    for c in text.chars() {
        translated.push_str(match c {
            'ß' => "ss",
            'æ' => "ae",
            'Æ' => "AE",
            'ø' => "o",
            'Ø' => "O",
            'đ' => "d",
            'Đ' => "D",
            'ł' => "l",
            'Ł' => "L",
            'œ' => "oe",
            'Œ' => "OE",
            'þ' => "th",
            'Þ' => "Th",
            'ð' => "d",
            'Ð' => "D",
            'ı' => "i",
            'ħ' => "h",
            'Ħ' => "H",
            _ => {
                translated.push(c);
                continue;
            }
        });
    }
    translated
        .nfd()
        .filter(|c| canonical_combining_class(*c) == 0)
        .collect()
}

fn frecency(record: &Usage, now: f64) -> f64 {
    if let Some(anchor) = record.anchor {
        return ((anchor - now) * std::f64::consts::LN_2 / HALF_LIFE)
            .min(700.0)
            .exp()
            .max(1.0);
    }
    let count = record.count.unwrap_or(0).min(1000000) as f64;
    ((1.0 + 100.0 * count) * 2.0_f64.powf(-(now - record.last.unwrap_or(0.0)).max(0.0) / HALF_LIFE))
        .max(1.0)
}

struct Prepared {
    title: String,
    subtitle: String,
    keywords: String,
    lower: String,
    alias: String,
    pin: Option<usize>,
}

#[derive(Default)]
pub struct Policy {
    catalog: Option<Catalog>,
    prepared: Vec<Prepared>,
}

struct Scorer {
    matcher: Matcher,
    letters: Vec<Letter>,
    state: State,
    edits: Scratch,
    high: bool,
}

impl Scorer {
    fn new(query: &str, high: bool) -> Self {
        let matcher = Matcher::new(query);
        let state = State::new(matcher.needle.len());
        Self {
            matcher,
            letters: Vec::new(),
            state,
            edits: Scratch::default(),
            high,
        }
    }
    fn score(&mut self, text: &str) -> Option<i32> {
        self.matcher
            .name_bytes(
                text.as_bytes(),
                &mut self.letters,
                &mut self.state,
                &mut self.edits,
            )
            .filter(|s| *s > SHADOW_TYPO && (!self.high || *s >= NAME - HIT_SPAN))
    }
}

struct Record<'a> {
    id: &'a str,
    title: &'a str,
    frequency: f64,
    is_app: bool,
    kind: i32,
    has_alias: bool,
    priority: [bool; 6],
    best: i32,
    title_score: i32,
    pin: Option<usize>,
}

impl Policy {
    pub fn load(&mut self, catalog: Catalog) -> Result<usize, &'static str> {
        catalog.validate()?;
        let pins: HashMap<_, _> = catalog
            .favorites
            .iter()
            .enumerate()
            .map(|(i, id)| (id.as_str(), i))
            .collect();
        let prepared = catalog
            .candidates
            .iter()
            .map(|row| {
                let title = transliterate(&row.title);
                Prepared {
                    lower: title.to_lowercase(),
                    title,
                    subtitle: transliterate(&row.subtitle),
                    keywords: transliterate(&row.keywords),
                    alias: transliterate(catalog.aliases.get(&row.id).map_or("", String::as_str))
                        .to_lowercase(),
                    pin: pins.get(row.id.as_str()).copied(),
                }
            })
            .collect();
        let count = catalog.candidates.len();
        self.prepared = prepared;
        self.catalog = Some(catalog);
        Ok(count)
    }

    pub fn rank(&self, p: Query, now: f64) -> Result<Vec<String>, &'static str> {
        p.validate()?;
        let catalog = self
            .catalog
            .as_ref()
            .ok_or("Load a catalog before ranking")?;
        if p.catalog_revision != catalog.catalog_revision {
            return Err("Ranking catalog revision is not loaded");
        }
        let query = transliterate(p.query.trim()).to_lowercase();
        let empty = query.is_empty();
        let mut scorer = Scorer::new(&query, matches!(p.sensitivity, Sensitivity::High));
        let default_usage = Usage::default();
        let mut records = Vec::new();
        // ponytail: one validated catalog per revision; scan only normalized in-memory rows per query.
        for (row, text) in catalog.candidates.iter().zip(&self.prepared) {
            let usage = catalog.usage.get(&row.id).unwrap_or(&default_usage);
            let frequency = frecency(usage, now);
            let alias = &text.alias;
            let mut record = Record {
                id: &row.id,
                title: &text.lower,
                frequency,
                is_app: row.kind == "app",
                kind: if row.kind == "app" {
                    5
                } else if row.kind == "mode" {
                    4
                } else {
                    1
                },
                has_alias: !alias.is_empty(),
                priority: [false; 6],
                best: i32::MIN,
                title_score: i32::MIN,
                pin: if empty { text.pin } else { None },
            };
            if !empty {
                let title = scorer.score(&text.title);
                let subtitle = scorer.score(&text.subtitle);
                let keyword = scorer.score(&text.keywords);
                let alias_prefix = !alias.is_empty() && alias.starts_with(&query);
                if !alias_prefix && title.is_none() && subtitle.is_none() && keyword.is_none() {
                    continue;
                }
                let terms = if p.learn_queries
                    && frequency > 1.0
                    && now - usage.last.unwrap_or(0.0) <= TERM_WINDOW
                {
                    usage.terms.as_slice()
                } else {
                    &[]
                };
                record.priority = [
                    alias == &query,
                    terms.iter().any(|t| t == &query),
                    text.lower == query,
                    subtitle == Some(EXACT),
                    alias_prefix,
                    terms.iter().any(|t| t.starts_with(&query)),
                ];
                record.title_score = title.unwrap_or(i32::MIN);
                record.best = title.max(subtitle).unwrap_or(i32::MIN);
            }
            records.push(record);
        }
        records.sort_by(|a, b| {
            let plain = || {
                b.frequency
                    .total_cmp(&a.frequency)
                    .then_with(|| b.has_alias.cmp(&a.has_alias))
                    .then_with(|| b.kind.cmp(&a.kind))
                    .then_with(|| a.title.cmp(b.title))
                    .then_with(|| a.id.cmp(b.id))
            };
            if empty {
                a.pin
                    .unwrap_or(usize::MAX)
                    .cmp(&b.pin.unwrap_or(usize::MAX))
                    // Apply empty-query section priority before the global result limit.
                    .then_with(|| a.is_app.cmp(&b.is_app))
                    .then_with(plain)
            } else {
                b.priority
                    .cmp(&a.priority)
                    .then_with(|| b.best.cmp(&a.best))
                    .then_with(|| b.frequency.total_cmp(&a.frequency))
                    .then_with(|| b.title_score.cmp(&a.title_score))
                    .then_with(|| b.kind.cmp(&a.kind))
                    .then_with(|| a.title.cmp(b.title))
                    .then_with(|| a.id.cmp(b.id))
            }
        });
        Ok(records
            .into_iter()
            .take(p.limit)
            .map(|r| r.id.to_owned())
            .collect())
    }
}
