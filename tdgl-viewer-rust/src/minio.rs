use crate::run_info::RunInfo;

pub struct MinioClient {
    endpoint: String,
    bucket: String,
    client: reqwest::blocking::Client,
}

impl MinioClient {
    pub fn new(endpoint: &str, bucket: &str) -> Self {
        MinioClient {
            endpoint: endpoint.trim_end_matches('/').to_string(),
            bucket: bucket.to_string(),
            client: reqwest::blocking::Client::new(),
        }
    }

    pub fn list_runs(&self) -> Result<Vec<RunInfo>, String> {
        let url = format!(
            "{}/{}?list-type=2&prefix=tdgl-runs/&delimiter=/",
            self.endpoint, self.bucket
        );
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        let body = resp.text().map_err(|e| e.to_string())?;
        let prefixes = extract_prefixes(&body);
        let mut runs = Vec::new();
        for prefix in prefixes {
            // Extract run_id from prefix like "tdgl-runs/abc-123/"
            let run_id = prefix
                .trim_end_matches('/')
                .trim_start_matches("tdgl-runs/")
                .to_string();
            if let Some(run) = self.get_manifest(&run_id)? {
                runs.push(run);
            }
        }
        runs.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        Ok(runs)
    }

    pub fn get_manifest(&self, run_id: &str) -> Result<Option<RunInfo>, String> {
        let url = format!(
            "{}/{}/tdgl-runs/{}/manifest.json",
            self.endpoint, self.bucket, run_id
        );
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        let body = resp.text().map_err(|e| e.to_string())?;
        let run: RunInfo = serde_json::from_str(&body).map_err(|e| e.to_string())?;
        Ok(Some(run))
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
        let range = format!("bytes={}-{}", offset, offset + length - 1);
        let resp = self
            .client
            .get(&url)
            .header("Range", &range)
            .send()
            .map_err(|e| e.to_string())?;
        let bytes = resp.bytes().map_err(|e| e.to_string())?;
        Ok(bytes.to_vec())
    }

    pub fn h5_key(&self, run_id: &str) -> String {
        format!("tdgl-runs/{}/output.h5", run_id)
    }

    pub fn viewer_index_key(&self, run_id: &str) -> String {
        format!("tdgl-runs/{}/viewer-index.json", run_id)
    }

    pub fn iv_key(&self, run_id: &str) -> String {
        format!("tdgl-runs/{}/iv.json", run_id)
    }

    /// Get the content-length of an object via HEAD request.
    /// Returns None if the object doesn't exist or the header is missing.
    pub fn object_size(&self, key: &str) -> Result<Option<u64>, String> {
        let url = format!("{}/{}/{}", self.endpoint, self.bucket, key);
        let resp = self.client.head(&url).send().map_err(|e| e.to_string())?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        Ok(resp.content_length())
    }

    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub fn bucket(&self) -> &str {
        &self.bucket
    }
}

fn extract_prefixes(xml: &str) -> Vec<String> {
    let mut prefixes = Vec::new();
    for part in xml.split("<CommonPrefixes>") {
        if let Some(end) = part.find("</CommonPrefixes>") {
            let inner = &part[..end];
            if let (Some(s), Some(e)) = (inner.find("<Prefix>"), inner.find("</Prefix>")) {
                prefixes.push(inner[s + 8..e].to_string());
            }
        }
    }
    prefixes
}

fn extract_manifest_key(xml: &str) -> Option<String> {
    for part in xml.split("<Contents>") {
        if let Some(end) = part.find("</Contents>") {
            let inner = &part[..end];
            if inner.contains("manifest.json") {
                if let (Some(s), Some(e)) = (inner.find("<Key>"), inner.find("</Key>")) {
                    return Some(inner[s + 5..e].to_string());
                }
            }
        }
    }
    None
}
