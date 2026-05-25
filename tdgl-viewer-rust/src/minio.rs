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
        let url = format!("{}/{}?list-type=2&prefix=tdgl-runs/&delimiter=/",
            self.endpoint, self.bucket);
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        let body = resp.text().map_err(|e| e.to_string())?;
        let prefixes = extract_prefixes(&body);
        let mut runs = Vec::new();
        for prefix in prefixes {
            if let Some(run) = self.get_manifest_by_prefix(&prefix)? {
                runs.push(run);
            }
        }
        runs.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        Ok(runs)
    }

    pub fn get_manifest(&self, run_id: &str) -> Result<Option<RunInfo>, String> {
        let url = format!("{}/{}/tdgl-runs/{}/manifest.json",
            self.endpoint, self.bucket, run_id);
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        if resp.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        let body = resp.text().map_err(|e| e.to_string())?;
        let run: RunInfo = serde_json::from_str(&body).map_err(|e| e.to_string())?;
        Ok(Some(run))
    }

    fn get_manifest_by_prefix(&self, prefix: &str) -> Result<Option<RunInfo>, String> {
        let url = format!("{}/{}?prefix={}&suffix=manifest.json&list-type=2",
            self.endpoint, self.bucket, prefix);
        let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
        let body = resp.text().map_err(|e| e.to_string())?;
        if let Some(key) = extract_manifest_key(&body) {
            let url = format!("{}/{}", self.endpoint, key);
            let resp = self.client.get(&url).send().map_err(|e| e.to_string())?;
            if resp.status() == reqwest::StatusCode::NOT_FOUND {
                return Ok(None);
            }
            let body = resp.text().map_err(|e| e.to_string())?;
            let run: RunInfo = serde_json::from_str(&body).map_err(|e| e.to_string())?;
            Ok(Some(run))
        } else {
            Ok(None)
        }
    }

    pub fn read_range(&self, key: &str, offset: u64, length: u64) -> Result<Vec<u8>, String> {
        let url = format!("{}/{}", self.endpoint, key);
        let range = format!("bytes={}-{}", offset, offset + length - 1);
        let resp = self.client.get(&url)
            .header("Range", &range)
            .send()
            .map_err(|e| e.to_string())?;
        let bytes = resp.bytes().map_err(|e| e.to_string())?;
        Ok(bytes.to_vec())
    }

    pub fn h5_key(&self, run_id: &str) -> String {
        format!("tdgl-runs/{}/output.h5", run_id)
    }

    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }
}

fn extract_prefixes(xml: &str) -> Vec<String> {
    let mut prefixes = Vec::new();
    for part in xml.split("<CommonPrefixes>") {
        if let Some(end) = part.find("</CommonPrefixes>") {
            let inner = &part[..end];
            if let (Some(s), Some(e)) = (inner.find("<Prefix>"), inner.find("</Prefix>")) {
                prefixes.push(inner[s+8..e].to_string());
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
                    return Some(inner[s+5..e].to_string());
                }
            }
        }
    }
    None
}