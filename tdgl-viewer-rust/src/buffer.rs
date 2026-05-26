use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex};

pub struct FrameBuffer {
    frames: Mutex<HashMap<usize, Vec<u8>>>,
    capacity: usize,
}

impl FrameBuffer {
    pub fn new(capacity: usize) -> Self {
        FrameBuffer {
            frames: Mutex::new(HashMap::new()),
            capacity,
        }
    }

    pub fn get(&self, idx: usize) -> Option<Vec<u8>> {
        self.frames.lock().unwrap().get(&idx).cloned()
    }

    pub fn insert(&self, idx: usize, png: Vec<u8>) {
        let mut frames = self.frames.lock().unwrap();
        frames.insert(idx, png);
        // Evict entries furthest from center — simple: evict oldest/smallest key
        while frames.len() > self.capacity {
            if let Some(k) = frames.keys().min().copied() {
                frames.remove(&k);
            }
        }
    }

    pub fn clear(&self) {
        self.frames.lock().unwrap().clear();
    }

    pub fn len(&self) -> usize {
        self.frames.lock().unwrap().len()
    }
}
