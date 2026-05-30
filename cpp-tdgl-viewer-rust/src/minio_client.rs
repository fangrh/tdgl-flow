#[derive(Clone, Debug)]
pub struct MinioClient {
    endpoint: String,
    bucket: String,
    client: reqwest::blocking::Client,
}

#[derive(Debug, Clone)]
pub struct ObjectInfo {
    pub content_length: Option<u64>,
}

impl MinioClient {
    pub fn new(endpoint: &str, bucket: &str) -> Self {
        MinioClient {
            endpoint: endpoint.trim_end_matches('/').to_string(),
            bucket: bucket.to_string(),
            client: reqwest::blocking::Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .unwrap_or_default(),
        }
    }

    #[allow(dead_code)]
    pub fn read_text(&self, key: &str) -> Result<String, String> {
        let url = format!("{}/{}/{}", self.endpoint, self.bucket, key);
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Err(format!("{} not found", key));
        }
        if !resp.status().is_success() {
            return Err(format!("GET {} failed: {}", key, resp.status()));
        }
        resp.text().map_err(|e| e.to_string())
    }

    pub fn read_text_optional(&self, key: &str) -> Result<Option<String>, String> {
        let url = format!("{}/{}/{}", self.endpoint, self.bucket, key);
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !resp.status().is_success() {
            return Err(format!("GET {} failed: {}", key, resp.status()));
        }
        resp.text().map(Some).map_err(|e| e.to_string())
    }

    pub fn read_range(&self, key: &str, offset: u64, length: u64) -> Result<Vec<u8>, String> {
        let url = format!("{}/{}/{}", self.endpoint, self.bucket, key);
        let range = format!("bytes={}-{}", offset, offset.saturating_add(length).saturating_sub(1));
        let resp = self
            .client
            .get(&url)
            .header("Range", &range)
            .send()
            .map_err(|e| e.to_string())?;

        let status = resp.status();
        if status.as_u16() == 416 {
            return Err(format!(
                "Range {}-{} not satisfiable",
                offset,
                offset.saturating_add(length).saturating_sub(1)
            ));
        }
        if !status.is_success() && status.as_u16() != 206 {
            return Err(format!("GET {} range failed: {}", key, status));
        }

        let bytes = resp.bytes().map_err(|e| e.to_string())?.to_vec();
        Ok(bytes)
    }

    pub fn object_info(&self, key: &str) -> Result<Option<ObjectInfo>, String> {
        let url = format!("{}/{}/{}", self.endpoint, self.bucket, key);
        eprintln!("[DEBUG] object_info HEAD url={}", url);
        let resp = self.client.head(&url).send().map_err(|e| {
            eprintln!("[DEBUG] HEAD error: {}", e);
            e.to_string()
        })?;
        let status = resp.status();
        let cl = resp.content_length();
        eprintln!("[DEBUG] HEAD status={} content_length={:?}", status, cl);
        if status == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        Ok(Some(ObjectInfo {
            content_length: cl,
        }))
    }

    pub fn object_size(&self, key: &str) -> Result<Option<u64>, String> {
        // HEAD Content-Length is unreliable in some setups (e.g., MinIO behind nginx).
        // Use a GET Range request for just 1 byte to get the actual Content-Range header.
        let url = format!("{}/{}/{}", self.endpoint, self.bucket, key);
        let resp = self.client
            .get(&url)
            .header("Range", "bytes=0-0")
            .send()
            .map_err(|e| e.to_string())?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        // Content-Range header format: "bytes 0-0/TOTAL_SIZE"
        if let Some(cr) = resp.headers().get("content-range") {
            let cr_str = cr.to_str().map_err(|e| e.to_string())?;
            // Parse "bytes 0-0/609488"
            if let Some(size_str) = cr_str.split('/').last() {
                if let Ok(size) = size_str.parse::<u64>() {
                    return Ok(Some(size));
                }
            }
        }
        // Fallback to content_length
        Ok(Some(resp.content_length().unwrap_or(0)))
    }

    #[allow(dead_code)]
    pub fn list_prefix(&self, prefix: &str) -> Result<Vec<String>, String> {
        let url = format!(
            "{}/{}?list-type=2&prefix={}&delimiter=/",
            self.endpoint, self.bucket, prefix
        );
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        let body = resp.text().map_err(|e| e.to_string())?;
        let mut keys = Vec::new();
        for part in body.split("<Contents>") {
            if let Some(end) = part.find("</Contents>") {
                let inner = &part[..end];
                if let (Some(s), Some(e)) = (inner.find("<Key>"), inner.find("</Key>")) {
                    keys.push(inner[s + 5..e].to_string());
                }
            }
        }
        Ok(keys)
    }
}
