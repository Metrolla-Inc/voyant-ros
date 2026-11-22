// Reference receiver for the smoke test (docker/smoke/smoke.sh): joins the same multicast stream
// as the driver with the Voyant client directly (no ROS) and writes one line per frame it
// assembles, so check_cloud.py can match the driver's point clouds with the SDK's own frames.
//
// Usage: frame_dump BIND_ADDR GROUP_ADDR INTERFACE_ADDR FRAMES OUT_FILE
// Line per frame: host receive time (ns), header seconds, header nanoseconds, frame index, number
// of points, number of points with drop_reason SUCCESS, then FNV-1a 64 of all points and of the
// SUCCESS points, each point packed little-endian as f32 x, y, z, radial velocity, snr; u8 drop
// reason; i32 timestamp_nanosecs; u32 point index (29 bytes, as check_cloud.py packs them).
// Exit 0 after FRAMES frames; 1 when the client does not start or no frame arrives for 60 s.
#include <voyant_client.hpp>

#include <chrono>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
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
} // namespace

int main(int argc, char **argv)
{
  if(argc != 6)
  {
    std::fprintf(stderr, "usage: frame_dump BIND GROUP IFACE FRAMES OUT\n");
    return 2;
  }
  const int frames = std::atoi(argv[4]);
  // std::string, not char*: with char* arguments the compiler picks the unicast constructor
  // VoyantClient(addr, bool filterPoints, bool disableSpn) (pointer-to-bool beats the conversion
  // to std::string) and the client silently listens for unicast with both flags set.
  const std::string bind(argv[1]), group(argv[2]), iface(argv[3]);
  VoyantClient client(bind, group, iface);
  if(!client.isValid())
  {
    std::fprintf(stderr, "frame_dump: client not valid\n");
    return 1;
  }
  std::FILE *out = std::fopen(argv[5], "w");
  if(out == nullptr)
  {
    std::perror(argv[5]);
    return 1;
  }
  int got = 0;
  auto last = std::chrono::steady_clock::now();
  while(got < frames)
  {
    if(!client.tryReceiveNextFrame())
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
    VoyantFrameWrapper &frame = client.latestFrame();
    Fnv all, ok;
    size_t n_ok = 0;
    for(const auto &p : frame.points())
    {
      unsigned char rec[29];
      const float f[5] = {p.x(), p.y(), p.z(), p.radial_vel(), p.snr_linear()};
      const uint8_t drop = static_cast<uint8_t>(p.drop_reason());
      const int32_t ts = p.timestamp_nanosecs();
      const uint32_t idx = p.point_index();
      std::memcpy(rec, f, 20);
      rec[20] = drop;
      std::memcpy(rec + 21, &ts, 4);
      std::memcpy(rec + 25, &idx, 4);
      all.add(rec, sizeof(rec));
      if(p.drop_reason() == DropReason::SUCCESS)
      {
        ok.add(rec, sizeof(rec));
        ++n_ok;
      }
    }
    std::fprintf(out,
                 "%" PRId64 " %u %u %u %zu %zu %016" PRIx64 " %016" PRIx64 " %s\n",
                 static_cast<int64_t>(now),
                 static_cast<unsigned>(frame.header().timestampSeconds()),
                 static_cast<unsigned>(frame.header().timestampNanoseconds()),
                 static_cast<unsigned>(frame.header().frameIndex()),
                 frame.points().size(),
                 n_ok,
                 all.h,
                 ok.h,
                 frame.header().deviceId().c_str());
    std::fflush(out);
    ++got;
  }
  std::fclose(out);
  std::fprintf(stderr, "frame_dump: %d frames\n", got);
  return 0;
}
