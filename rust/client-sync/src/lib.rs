use reqwest::{Method, blocking::Client};
use serde_json::Value;
use std::io::{self, BufRead, Write};

/// Read dot-terminated text, using SMTP-style dot stuffing for literal leading dots.
pub fn read_multiline<R: BufRead, W: Write>(reader: &mut R, writer: &mut W) -> io::Result<String> {
    writeln!(
        writer,
        "text (a single '.' ends input; add an extra '.' to lines starting with '.'):"
    )?;
    writer.flush()?;

    let mut lines = Vec::new();
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            return Err(io::ErrorKind::UnexpectedEof.into());
        }
        let line = line.trim_end_matches(['\r', '\n']);
        if line == "." {
            break;
        }
        lines.push(line.strip_prefix('.').unwrap_or(line).to_owned());
    }
    Ok(lines.join("\n"))
}

/// Preserve HTTP status even when the error body is not JSON.
pub fn exchange(
    client: &Client,
    url: &str,
    method: Method,
    path: &str,
    token: &str,
    body: Option<&Value>,
) -> Result<(u16, Value), reqwest::Error> {
    let mut request = client.request(method, format!("{}{path}", url.trim_end_matches('/')));
    if !token.is_empty() {
        request = request.bearer_auth(token);
    }
    if let Some(body) = body {
        request = request.json(body);
    }
    let response = request.send()?;
    let status = response.status().as_u16();
    let text = response.text()?;
    let value =
        serde_json::from_str(&text).unwrap_or_else(|_| serde_json::json!({"message": text}));
    Ok((status, value))
}

#[cfg(test)]
mod tests {
    use super::read_multiline;
    use std::io::Cursor;

    #[test]
    fn multiline_input_preserves_unicode_trailing_newline_and_marker_line() {
        let mut input = Cursor::new("你好\n..\n\n.\n");
        let mut output = Vec::new();

        let text = read_multiline(&mut input, &mut output).unwrap();

        assert_eq!(text, "你好\n.\n");
    }

    #[test]
    fn multiline_input_accepts_empty_text() {
        let mut input = Cursor::new(".\n");
        let mut output = Vec::new();

        assert_eq!(read_multiline(&mut input, &mut output).unwrap(), "");
    }
}
