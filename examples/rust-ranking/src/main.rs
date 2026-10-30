//! Persistent KITE API-v2 ranking backend. No JavaScript or host services.
#[allow(dead_code)]
mod backend;
mod policy;

use policy::{Catalog, Params, Policy, Query};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{self, BufRead, Read, Write},
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

const MAX_FRAME: usize = 4 * 1024 * 1024;
const SAFE: u64 = 1 << 53;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    jsonrpc: String,
    #[serde(default)]
    id: Option<Value>,
    method: String,
    #[serde(default = "empty")]
    params: Value,
}
fn empty() -> Value {
    json!({})
}
fn valid_id(id: &Value) -> bool {
    match id {
        Value::String(s) => !s.is_empty() && s.chars().count() <= 96,
        Value::Number(n) => n.as_u64().is_some_and(|n| n < SAFE),
        _ => false,
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Initialize {
    api_version: u32,
    wire_version: u32,
    plugin_id: String,
    directory: String,
    config: HashMap<String, Value>,
    environment: HashMap<String, String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Cancel {
    id: Value,
}

#[derive(Default)]
struct Server {
    initialized: bool,
    policy: Policy,
}

impl Server {
    fn handle(&mut self, request: &Request) -> Result<Value, &'static str> {
        match request.method.as_str() {
            "initialize" => {
                let p: Initialize = serde_json::from_value(request.params.clone())
                    .map_err(|_| "Invalid initialization")?;
                if self.initialized
                    || p.api_version != 2
                    || p.wire_version != 1
                    || p.plugin_id != "rust-ranking"
                    || !Path::new(&p.directory).is_absolute()
                    || p.directory.contains('\0')
                    || p.environment
                        .iter()
                        .any(|(k, v)| k.contains(['\0', '=']) || v.contains('\0'))
                {
                    return Err("Unsupported initialization or package identity");
                }
                // Config/environment are deliberately neither logged nor retained by this read-only ranker.
                drop(p.config);
                self.initialized = true;
                Ok(
                    json!({"api_version": 2, "wire_version": 1, "plugin_id": p.plugin_id, "has_handler": false, "ranking_version": 2}),
                )
            }
            "catalog" => {
                if !self.initialized {
                    return Err("Initialize before loading a catalog");
                }
                let catalog: Catalog = serde_json::from_value(request.params.clone())
                    .map_err(|_| "Invalid catalog types")?;
                let revision = catalog.catalog_revision;
                let count = self.policy.load(catalog)?;
                Ok(json!({"catalog_revision": revision, "count": count}))
            }
            "rank" => {
                if !self.initialized {
                    return Err("Initialize before ranking");
                }
                let query: Query = if request
                    .params
                    .get("protocol_version")
                    .and_then(Value::as_u64)
                    == Some(1)
                {
                    let p: Params = serde_json::from_value(request.params.clone())
                        .map_err(|_| "Invalid legacy ranking parameters")?;
                    let (catalog, query) = p.split()?;
                    query.validate()?;
                    self.policy.load(catalog)?;
                    query
                } else {
                    serde_json::from_value(request.params.clone())
                        .map_err(|_| "Invalid ranking query types")?
                };
                let revision = query.catalog_revision;
                let now = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map_err(|_| "Clock predates Unix epoch")?
                    .as_secs_f64();
                let ordered = self.policy.rank(query, now)?;
                Ok(json!({"catalog_revision": revision, "ordered_ids": ordered}))
            }
            _ => Err("Unsupported method"),
        }
    }
}

// Reject duplicate keys recursively rather than letting serde_json silently keep the last value.
struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct Visitor;
        impl<'de> serde::de::Visitor<'de> for Visitor {
            type Value = Strict;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("bounded JSON")
            }
            fn visit_bool<E: serde::de::Error>(self, v: bool) -> Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_f64<E: serde::de::Error>(self, v: f64) -> Result<Strict, E> {
                serde_json::Number::from_f64(v)
                    .map(|n| Strict(Value::Number(n)))
                    .ok_or_else(|| E::custom("nonfinite number"))
            }
            fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_unit<E: serde::de::Error>(self) -> Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> Result<Strict, A::Error> {
                let mut values = Vec::new();
                while let Some(Strict(value)) = seq.next_element()? {
                    values.push(value);
                }
                Ok(Strict(Value::Array(values)))
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(
                self,
                mut map: A,
            ) -> Result<Strict, A::Error> {
                let mut values = serde_json::Map::new();
                while let Some((key, Strict(value))) = map.next_entry::<String, Strict>()? {
                    if values.insert(key, value).is_some() {
                        return Err(serde::de::Error::custom("duplicate key"));
                    }
                }
                Ok(Strict(Value::Object(values)))
            }
        }
        deserializer.deserialize_any(Visitor)
    }
}

fn parse(line: &[u8]) -> Result<Value, &'static str> {
    let (mut quoted, mut escaped, mut depth) = (false, false, 0u32);
    for &byte in line {
        if quoted {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                quoted = false;
            }
        } else if byte == b'"' {
            quoted = true;
        } else if byte == b'{' || byte == b'[' {
            depth += 1;
            if depth > 64 {
                return Err("JSON exceeds depth limit");
            }
        } else if byte == b'}' || byte == b']' {
            depth = depth.saturating_sub(1);
        }
    }
    serde_json::from_slice::<Strict>(line)
        .map(|v| v.0)
        .map_err(|_| "Invalid JSON or duplicate key")
}

fn read_frame(reader: &mut impl BufRead, line: &mut Vec<u8>) -> io::Result<Option<bool>> {
    line.clear();
    if reader
        .by_ref()
        .take((MAX_FRAME + 1) as u64)
        .read_until(b'\n', line)?
        == 0
    {
        return Ok(None);
    }
    if line.last() == Some(&b'\n') {
        line.pop();
        return Ok(Some(true));
    }
    if line.len() <= MAX_FRAME {
        return Ok(Some(true));
    }
    // Drain oversized input without allocating the rest of the frame.
    loop {
        let chunk = reader.fill_buf()?;
        if chunk.is_empty() {
            break;
        }
        let end = chunk.iter().position(|b| *b == b'\n');
        let consumed = end.map_or(chunk.len(), |at| at + 1);
        reader.consume(consumed);
        if end.is_some() {
            break;
        }
    }
    Ok(Some(false))
}

fn run(reader: &mut impl BufRead, writer: &mut impl Write) -> io::Result<()> {
    let mut server = Server::default();
    let mut line = Vec::new();
    while let Some(valid_size) = read_frame(reader, &mut line)? {
        if !valid_size {
            eprintln!("Rejected oversized RPC frame");
            continue;
        }
        let value = match parse(&line) {
            Ok(value) => value,
            Err(error) => {
                eprintln!("{error}");
                continue;
            }
        };
        let has_id = value.get("id").is_some();
        let id = value.get("id").filter(|id| valid_id(id)).cloned();
        let decoded: Result<Request, _> = serde_json::from_value(value);
        let outcome = match decoded {
            Ok(request) if request.jsonrpc == "2.0" && request.params.is_object() => {
                if !has_id && request.method == "shutdown" && request.params == empty() {
                    break;
                }
                if !has_id && request.method == "cancel" {
                    if let Ok(cancel) = serde_json::from_value::<Cancel>(request.params) {
                        if valid_id(&cancel.id) {
                            continue;
                        }
                    }
                    eprintln!("Invalid cancellation");
                    continue;
                }
                if !request.id.as_ref().is_some_and(valid_id) {
                    eprintln!("Missing or invalid RPC ID");
                    continue;
                }
                server.handle(&request)
            }
            _ => Err("Invalid JSON-RPC request"),
        };
        if let Some(id) = id {
            let reply = match outcome {
                Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
                Err(message) => {
                    json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32000, "message": message}})
                }
            };
            serde_json::to_writer(&mut *writer, &reply)?;
            writer.write_all(b"\n")?;
            writer.flush()?;
        }
    }
    Ok(())
}

fn main() -> io::Result<()> {
    run(&mut io::stdin().lock(), &mut io::stdout().lock())
}
