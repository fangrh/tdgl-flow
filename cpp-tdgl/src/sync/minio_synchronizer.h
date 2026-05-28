#pragma once
#include <string>
#include <thread>
#include <mutex>
#include <atomic>
#include <queue>
#include <functional>

class MinioSynchronizer {
public:
    MinioSynchronizer(const std::string& url, const std::string& bucket,
                     const std::string& prefix);
    ~MinioSynchronizer();

    void start();
    void stop();
    void upload_file(const std::string& local_path, const std::string& minio_key);
    void upload_step(const std::string& step_h5_path, int step_idx);
    void upload_index(const std::string& index_path);
    void upload_mesh(const std::string& mesh_path);
    void upload_manifest(const std::string& manifest_path);

    bool is_running() const { return running_; }

private:
    void run_loop();
    void enqueue_upload(const std::string& local_path, const std::string& minio_key);

    std::string url_;
    std::string bucket_;
    std::string prefix_;
    std::atomic<bool> running_{false};
    std::thread thread_;
    std::mutex mutex_;
    std::queue<std::pair<std::string, std::string>> upload_queue_;
};