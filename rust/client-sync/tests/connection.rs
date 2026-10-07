use reqwest::{Method, blocking::Client};
use rm_client_sync::exchange;
use serde_json::json;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::time::Duration;

fn test_client() -> Client {
    Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap()
}

#[test]
fn sends_http_authorization_and_preserves_error_status() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let peer = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut headers = String::new();
        loop {
            let mut line = String::new();
            assert!(reader.read_line(&mut line).unwrap() > 0);
            if line == "\r\n" {
                break;
            }
            headers.push_str(&line);
        }
        assert!(headers.starts_with("GET /texts HTTP/1.1\r\n"));
        assert!(
            headers
                .to_lowercase()
                .contains("authorization: bearer sample\r\n")
        );
        stream.write_all(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 7\r\nConnection: close\r\n\r\nexpired").unwrap();
    });
    let client = test_client();
    let result = exchange(&client, &url, Method::GET, "/texts", "sample", None).unwrap();
    assert_eq!(result, (401, json!({"message":"expired"})));
    peer.join().unwrap();
}

#[test]
fn sends_echo_text_as_json_and_preserves_response() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let peer = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut headers = String::new();
        loop {
            let mut line = String::new();
            assert!(reader.read_line(&mut line).unwrap() > 0);
            if line == "\r\n" {
                break;
            }
            headers.push_str(&line);
        }
        assert!(headers.starts_with("POST /echo HTTP/1.1\r\n"));
        assert!(
            headers
                .to_lowercase()
                .contains("content-type: application/json\r\n")
        );
        let content_length = headers
            .lines()
            .find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse::<usize>().unwrap())
            })
            .unwrap();
        let mut body = vec![0; content_length];
        reader.read_exact(&mut body).unwrap();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&body).unwrap(),
            json!({"text": "你好\n.\n"})
        );

        let response = serde_json::to_vec(&json!({"data": "你好\n.\n"})).unwrap();
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            response.len()
        )
        .unwrap();
        stream.write_all(&response).unwrap();
    });

    let result = exchange(
        &test_client(),
        &url,
        Method::POST,
        "/echo",
        "",
        Some(&json!({"text": "你好\n.\n"})),
    )
    .unwrap();

    assert_eq!(result, (200, json!({"data": "你好\n.\n"})));
    peer.join().unwrap();
}
