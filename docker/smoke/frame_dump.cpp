// Reference receiver for the smoke test (docker/smoke/smoke.sh): receives the same stream as the
// driver with voyant-api's CarbonClient directly (no ROS) and writes one line per frame, so
// check_cloud.py can match the driver's point clouds with the SDK's own frames.
//
// Usage: frame_dump BIND_ADDR GROUP_ADDR INTERFACE_ADDR FPGA_ADDR FRAMES OUT_FILE [diagnostic]
// The client is an observer (observer_only: it sends nothing to the sensor, so it never competes
// with the driver, the primary client) on multicast transport; "diagnostic" keeps the invalid
// points (diagnostic_mode), so the driver's valid-only clouds can be matched with the valid subset.
//
// Line per frame: host receive time (ns), frame seconds, frame nanoseconds, frame index, number
// of points, number of valid points (drop_reason 1), FNV-1a 64 of all points and of the valid
// points, device id. Each point is hashed as the driver publishes it (include/voyant_ros/
// conversion_utils.hpp, fillPointFromFrame): little-endian f32 x, y, z (NaN when range_m <= 0),
// f32 v (doppler), f32 snr, u8 drop_reason, u32 timestamp_nsecs, u16 azimuth_idx,
// u16 elevation_idx, f32 calibrated_reflectance, u32 frame_index (37 bytes; check_cloud.py
// takes the same bytes out of each 48-byte ROS point).
// Exit 0 after FRAMES frames; 1 when the client does not start or no frame arrives for 60 s.
#include <carbon_client.hpp>
#include <carbon_config.hpp>
#include <logging_utils_ffi.hpp>

#include <chrono>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>
#include <thread>

namespace
{
struct Fnv
{
  uint64_t h = 1469598103934665603ULL;
  void add(const void *data, size_t n)
  {
    const auto *p = static_cast<const unsigned char *>(data);
    for(size_t i = 0; i < n; ++i)
    {
      h ^= p[i];
      h *= 1099511628211ULL;
    }
  }
};

void pack(unsigned char *rec, const PointData &p, const VoyantVec3 &xyz, uint32_t frame_index)
{
  float pos[3] = {xyz.x, xyz.y, xyz.z};
  if(!(p.range_m > 0.0f))
  {
    pos[0] = pos[1] = pos[2] = std::numeric_limits<float>::quiet_NaN();
  }
  const float vs[2] = {p.doppler_mps, p.snr};
  std::memcpy(rec, pos, 12);
  std::memcpy(rec + 12, vs, 8);
  rec[20] = p.drop_reason;
  std::memcpy(rec + 21, &p.timestamp_nanosecs, 4);
  std::memcpy(rec + 25, &p.azimuth_idx, 2);
  std::memcpy(rec + 27, &p.elevation_idx, 2);
  std::memcpy(rec + 29, &p.calibrated_reflectance, 4);
  std::memcpy(rec + 33, &frame_index, 4);
}
} // namespace

int main(int argc, char **argv)
{
  if(argc != 7 && !(argc == 8 && std::strcmp(argv[7], "diagnostic") == 0))
  {
    std::fprintf(stderr, "usage: frame_dump BIND GROUP IFACE FPGA FRAMES OUT [diagnostic]\n");
    return 2;
  }
  voyant_log_init_c();
  CarbonClient::setupSignalHandling(SIGINT);
  CarbonClient::setupSignalHandling(SIGTERM);
  const int frames = std::atoi(argv[5]);
  CarbonConfig config;
  config.setBindAddr(argv[1])
      .setGroupAddr(argv[2])
      .setInterfaceAddr(argv[3])
      .setFpgaTargetAddr(argv[4])
      .setStreamTransport(StreamTransport::Multicast)
      .setObserverOnly(true)
      .setDiagnosticMode(argc == 8);
  CarbonClient client(config);
  if(!client.start())
  {
    std::fprintf(stderr, "frame_dump: the Carbon client did not start\n");
    return 1;
  }
  std::FILE *out = std::fopen(argv[6], "w");
  if(out == nullptr)
  {
    std::perror(argv[6]);
    return 1;
  }
  int got = 0;
  auto last = std::chrono::steady_clock::now();
  while(got < frames && client.isRunning() && !CarbonClient::isTerminated())
  {
    auto frame = client.tryReceiveFrame();
    if(!frame)
    {
      if(std::chrono::steady_clock::now() - last > std::chrono::seconds(60))
      {
        std::fprintf(stderr, "frame_dump: no frame for 60 s after %d frames\n", got);
        std::fclose(out);
        return 1;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
      continue;
    }
    last = std::chrono::steady_clock::now();
    const auto now = std::chrono::duration_cast<std::chrono::nanoseconds>(
                         std::chrono::system_clock::now().time_since_epoch())
                         .count();
    const std::vector<PointData> &points = frame->points();
    const std::vector<VoyantVec3> xyz = frame->xyz();
    Fnv all, ok;
    size_t n_ok = 0;
    for(size_t i = 0; i < points.size(); ++i)
    {
      unsigned char rec[37];
      pack(rec, points[i], xyz[i], frame->frameIndex());
      all.add(rec, sizeof(rec));
      if(isValidPoint(points[i]))
      {
        ok.add(rec, sizeof(rec));
        ++n_ok;
      }
    }
    std::fprintf(out,
                 "%" PRId64 " %" PRId64 " %d %u %zu %zu %016" PRIx64 " %016" PRIx64 " %s\n",
                 static_cast<int64_t>(now),
                 static_cast<int64_t>(frame->timestampSeconds()),
                 static_cast<int>(frame->timestampNanoseconds()),
                 static_cast<unsigned>(frame->frameIndex()),
                 points.size(),
                 n_ok,
                 all.h,
                 ok.h,
                 frame->deviceId().c_str());
    std::fflush(out);
    ++got;
  }
  client.stop();
  std::fclose(out);
  std::fprintf(stderr, "frame_dump: %d frames\n", got);
  return got == frames ? 0 : 1;
}
