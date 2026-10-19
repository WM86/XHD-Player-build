# -*- coding: utf-8 -*-
"""Make the XHD exposure readback asynchronous (one batch of latency).

The shipped core drains the whole GPU queue (scheduler.Finish()) on every
download batch. On slower CPUs/host that wait alone costs ~8 ms per batch,
which lands every heavy frame on the 33 ms step (30 FPS).

This patch keeps the exact same readback data path, but publishes the copies
and waits for them on a later batch instead of the current one.
"""

import sys
from pathlib import Path

HEADER_ANCHOR = "    std::unordered_set<ImageId> download_images;"
HEADER_MEMBER = """    std::unordered_set<ImageId> download_images;
#if defined(XHD_ONLY_RUNTIME)
    struct XhdDeferredReadbacks;
    std::unique_ptr<XhdDeferredReadbacks> xhd_readbacks;
    void XhdRetireReadbacks(bool wait_all);
#endif"""

CPP_INCLUDE_ANCHOR = """#if defined(XHD_ONLY_RUNTIME)
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <set>
#endif"""
CPP_INCLUDE_NEW = """#if defined(XHD_ONLY_RUNTIME)
#include <array>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <set>
#include <vector>
#endif"""

CPP_STRUCT_ANCHOR = "static constexpr u32 MAX_IMAGES = std::numeric_limits<u16>::max();"
CPP_STRUCT_NEW = """#if defined(XHD_ONLY_RUNTIME)
struct TextureCache::XhdDeferredReadbacks {
    struct Target {
        VAddr address{};
        u32 size{};
        u64 offset{};
    };

    struct Slot {
        Vulkan::StagingBufferRef staging{};
        u64 tick{};
        bool in_flight{};
        std::vector<Target> targets;
    };

    std::array<Slot, 2> slots{};
    size_t next{};
    u64 deferred_waits{};
};

void TextureCache::XhdRetireReadbacks(bool wait_all) {
    if (!xhd_readbacks) {
        xhd_readbacks = std::make_unique<XhdDeferredReadbacks>();
        return;
    }
    auto& pool = runtime.GetStagingPool();
    for (auto& slot : xhd_readbacks->slots) {
        if (!slot.in_flight) {
            continue;
        }
        if (!wait_all && !scheduler.IsFree(slot.tick)) {
            continue;
        }
        if (wait_all) {
            scheduler.Wait(slot.tick);
        }
        slot.staging.Invalidate();
        for (const auto& target : slot.targets) {
            Core::Memory::Instance()->TryWriteBacking(std::bit_cast<u8*>(target.address),
                                                      slot.staging.mapped + target.offset,
                                                      target.size);
        }
        pool.FreeDeferred(slot.staging);
        slot.staging = {};
        slot.targets.clear();
        slot.in_flight = false;
    }
}
#endif

static constexpr u32 MAX_IMAGES = std::numeric_limits<u16>::max();"""

CPP_DTOR_ANCHOR = "TextureCache::~TextureCache() = default;"
CPP_DTOR_NEW = """TextureCache::~TextureCache() {
#if defined(XHD_ONLY_RUNTIME)
    if (xhd_readbacks) {
        for (const auto& slot : xhd_readbacks->slots) {
            if (slot.in_flight) {
                scheduler.Finish();
                break;
            }
        }
    }
#endif
}"""

CPP_LOG_ANCHOR = """                             "XHD_COLOR ReadbackBatch batches={} images={} bytes={} waits={} mean_ms={}",
                             batches, images, bytes, batches, milliseconds / batches);"""
CPP_LOG_NEW = """                             "XHD_COLOR ReadbackBatch batches={} images={} bytes={} "
                             "deferred={} mean_ms={}",
                             batches, images, bytes, xhd_readbacks->deferred_waits,
                             milliseconds / batches);"""

CPP_OLD_BODY = """        if (!pending.empty()) {
            const auto start = std::chrono::steady_clock::now();
            // One allocation prevents reuse of a staging slice before the combined wait.
            const auto download = runtime.GetStagingPool().Request(total_size, MemoryType::HostCached, 16);
            for (const auto& item : pending) {
                auto& image = slot_images[item.image_id];
                const auto copy = ImageDownloadCopy(image, download.offset + item.offset);
                runtime.DownloadImage(&image, download.buffer, std::span{&copy, 1});
            }
            // Preserve the original synchronous visibility at OnSubmit/OnFence: all copies
            // complete and reach guest backing memory before returning, with one GPU wait.
            scheduler.Finish();
            download.Invalidate();
            for (const auto& item : pending) {
                Core::Memory::Instance()->TryWriteBacking(std::bit_cast<u8*>(item.address),
                                                          download.mapped + item.offset, item.size);
            }
            static const bool trace = [] {"""

CPP_NEW_BODY = """        if (!pending.empty()) {
            const auto start = std::chrono::steady_clock::now();
            XhdDeferredReadbacks::Slot* slot = nullptr;
            for (auto& candidate : xhd_readbacks->slots) {
                if (!candidate.in_flight) {
                    slot = &candidate;
                    break;
                }
            }
            if (slot == nullptr) {
                // Both slots are still on the GPU. Drain the older one instead of allocating more.
                XhdRetireReadbacks(true);
                ++xhd_readbacks->deferred_waits;
                slot = &xhd_readbacks->slots[xhd_readbacks->next];
            }
            xhd_readbacks->next = (xhd_readbacks->next + 1) % xhd_readbacks->slots.size();
            slot->targets.clear();
            // Deferred allocation: the pool keeps this buffer out of circulation while held.
            slot->staging =
                runtime.GetStagingPool().Request(total_size, MemoryType::HostCached, 16, true);
            for (const auto& item : pending) {
                auto& image = slot_images[item.image_id];
                const auto copy = ImageDownloadCopy(image, slot->staging.offset + item.offset);
                runtime.DownloadImage(&image, slot->staging.buffer, std::span{&copy, 1});
            }
            slot->targets.reserve(pending.size());
            for (const auto& item : pending) {
                slot->targets.push_back({item.address, item.size, item.offset});
            }
            // Publish the copies and let the GPU run them without draining the queue. The guest
            // receives this batch one step later, which is invisible at 60 Hz.
            scheduler.Flush();
            slot->tick = scheduler.CurrentTick();
            slot->in_flight = true;
            static const bool trace = [] {"""

CPP_PREPEND_ANCHOR = """        if (!pending.empty()) {
            const auto start = std::chrono::steady_clock::now();
            XhdDeferredReadbacks::Slot* slot = nullptr;"""

CPP_INIT_ANCHOR = """        std::vector<Pending> pending;
        u64 total_size = 0;"""
CPP_INIT_NEW = """        if (!xhd_readbacks) {
            xhd_readbacks = std::make_unique<XhdDeferredReadbacks>();
        }
        // Retire a batch whose copies the GPU has already finished. Never blocks here.
        XhdRetireReadbacks(false);

        std::vector<Pending> pending;
        u64 total_size = 0;"""

CPP_TAIL_ANCHOR = """                if (batches == 1 || batches % 120 == 0) {
                    LOG_INFO(Render_Vulkan,
                             "XHD_COLOR ReadbackBatch batches={} images={} bytes={} "
                             "deferred={} mean_ms={}",
                             batches, images, bytes, xhd_readbacks->deferred_waits,
                             milliseconds / batches);
                }
            }
        }
        download_images.clear();
        return;"""

CPP_TAIL_NEW = """                if (batches == 1 || batches % 120 == 0) {
                    LOG_INFO(Render_Vulkan,
                             "XHD_COLOR ReadbackBatch batches={} images={} bytes={} "
                             "deferred={} mean_ms={}",
                             batches, images, bytes, xhd_readbacks->deferred_waits,
                             milliseconds / batches);
                }
            }
        }
        download_images.clear();
        return;"""


def read(path):
    return path.read_text(encoding='utf-8')


def write(path, text):
    path.write_text(text, encoding='utf-8', newline='\n')


def replace_once(text, old, new, what):
    if old not in text:
        raise SystemExit('anchor not found: %s' % what)
    return text.replace(old, new, 1)


def main():
    root = Path(sys.argv[1])
    header = root / 'reference/shadPS4/src/video_core/texture_cache/texture_cache.h'
    cpp = root / 'reference/shadPS4/src/video_core/texture_cache/texture_cache.cpp'
    if not header.exists() or not cpp.exists():
        raise SystemExit('texture_cache sources not found under %s' % root)

    h = read(header)
    if 'xhd_readbacks' in h:
        raise SystemExit('header already patched')
    h = replace_once(h, '#include <mutex>', '#include <memory>\n#include <mutex>', 'header include')
    h = replace_once(h, HEADER_ANCHOR, HEADER_MEMBER, 'header members')
    write(header, h)

    c = read(cpp)
    if 'xhd_readbacks' in c:
        raise SystemExit('cpp already patched')
    c = replace_once(c, CPP_INCLUDE_ANCHOR, CPP_INCLUDE_NEW, 'cpp includes')
    c = replace_once(c, CPP_STRUCT_ANCHOR, CPP_STRUCT_NEW, 'cpp struct')
    c = replace_once(c, CPP_DTOR_ANCHOR, CPP_DTOR_NEW, 'cpp destructor')
    c = replace_once(c, CPP_LOG_ANCHOR, CPP_LOG_NEW, 'cpp log format')
    c = replace_once(c, CPP_OLD_BODY, CPP_NEW_BODY, 'cpp batch body')
    c = replace_once(c, CPP_INIT_ANCHOR, CPP_INIT_NEW, 'cpp slot init')
    write(cpp, c)

    print('patched: exposure readback is now deferred by one batch')
    print('header:', header)
    print('cpp   :', cpp)


if __name__ == '__main__':
    main()
