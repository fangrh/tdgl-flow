#include "sync/minio_synchronizer.h"
#include <curl/curl.h>
#include <fstream>
#include <cstdio>

MinioSynchronizer::MinioSynchronizer(const std::string& url, const std::string& bucket,
                                     const std::string& prefix)
    : url_(url), bucket_(bucket), prefix_(prefix) {
}

MinioSynchronizer::~MinioSynchronizer() {
    stop();
}

void MinioSynchronizer::start() {
    if (running_) return;
    running_ = true;
    thread_ = std::thread(&MinioSynchronizer::run_loop, this);
}

void MinioSynchronizer::stop() {
    if (!running_) return;
    running_ = false;
    if (thread_.joinable()) {
        thread_.join();
    }
}

void MinioSynchronizer::enqueue_upload(const std::string& local_path, const std::string& minio_key) {
    std::lock_guard<std::mutex> lock(mutex_);
    upload_queue_.push({local_path, minio_key});
}

void MinioSynchronizer::upload_file(const std::string& local_path, const std::string& minio_key) {
    enqueue_upload(local_path, minio_key);
}

void MinioSynchronizer::upload_step(const std::string& step_h5_path, int step_idx) {
    char buf[256];
    snprintf(buf, sizeof(buf), "step_%04d.h5", step_idx);
    std::string key = prefix_.empty() ? std::string(buf) : prefix_ + "/" + std::string(buf);
    upload_file(step_h5_path, key);
}

void MinioSynchronizer::upload_index(const std::string& index_path) {
    std::string key = prefix_.empty() ? "discrete_index.json" : prefix_ + "/discrete_index.json";
    upload_file(index_path, key);
}

void MinioSynchronizer::upload_mesh(const std::string& mesh_path) {
    std::string key = prefix_.empty() ? "mesh.h5" : prefix_ + "/mesh.h5";
    upload_file(mesh_path, key);
}

void MinioSynchronizer::upload_manifest(const std::string& manifest_path) {
    std::string key = prefix_.empty() ? "manifest.json" : prefix_ + "/manifest.json";
    upload_file(manifest_path, key);
}

void MinioSynchronizer::run_loop() {
    curl_global_init(CURL_GLOBAL_DEFAULT);
    while (running_) {
        std::pair<std::string, std::string> task;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (upload_queue_.empty()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
                continue;
            }
            task = upload_queue_.front();
            upload_queue_.pop();
        }

        const std::string& local_path = task.first;
        const std::string& minio_key = task.second;

        FILE* f = std::fopen(local_path.c_str(), "rb");
        if (!f) {
            fprintf(stderr, "[sync] Failed to open file for upload: %s\n", local_path.c_str());
            continue;
        }

        std::fseek(f, 0, SEEK_END);
        long file_size = std::ftell(f);
        std::fseek(f, 0, SEEK_SET);

        std::string url = url_ + "/" + bucket_ + "/" + minio_key;

        CURL* curl = curl_easy_init();
        if (curl) {
            curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
            curl_easy_setopt(curl, CURLOPT_UPLOAD, 1L);
            curl_easy_setopt(curl, CURLOPT_READDATA, f);
            curl_easy_setopt(curl, CURLOPT_INFILESIZE, file_size);
            curl_easy_setopt(curl, CURLOPT_PUT, 1L);
            // Minimal timeout to avoid hanging
            curl_easy_setopt(curl, CURLOPT_TIMEOUT, 60L);
            curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);

            CURLcode res = curl_easy_perform(curl);
            if (res != CURLE_OK) {
                fprintf(stderr, "[sync] Upload failed for %s: %s\n", local_path.c_str(), curl_easy_strerror(res));
            } else {
                fprintf(stderr, "[sync] Uploaded: %s -> %s\n", local_path.c_str(), minio_key.c_str());
            }
            curl_easy_cleanup(curl);
        }
        std::fclose(f);
    }
    curl_global_cleanup();
}