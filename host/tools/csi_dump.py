#!/usr/bin/env python3
"""RX direct RAW dump -> CSI buf word statistics -> 56SC index table finalization helper.

Connect directly to RX COM with bridge stopped:
  python csi_dump.py --port COM24 --count 300 --out dump.json

Output: buf_len/sig_mode/first_word_invalid distribution, per-word average amplitude/zero-rate table,
amplitude-based valid word 56 proposal and comparison with firmware provisional table (SC_WORD_HTLTF).
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi_host.framing import StreamParser  # noqa: E402

# Firmware provisional table (same as firmware/rx/main/sc_table.h -- measured comparison baseline)
SC_WORD_HTLTF = list(range(100, 128)) + list(range(65, 93))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--out", default="dump.json")
    args = ap.parse_args()

    import serial

    frames = []
    parser = StreamParser()
    with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
        ser.reset_input_buffer()
        ser.write(f"RAW {args.count}\n".encode())
        deadline = time.monotonic() + args.timeout
        while len(frames) < args.count and time.monotonic() < deadline:
            chunk = ser.read(4096)
            if not chunk:
                continue
            for kind, val in parser.feed(chunk):
                if kind == "rawframe":
                    frames.append(val)
                elif kind == "text":
                    print(f"[{args.port}] {val}")

    if not frames:
        print("ERROR: 0 rawframes -- check TX active/channel/SET_IDX", file=sys.stderr)
        sys.exit(1)

    buf_lens = Counter(f.buf_len for f in frames)
    sig_modes = Counter((f.flags >> 1) & 3 for f in frames)
    fwi = sum(1 for f in frames if f.flags & 1)
    max_words = max(f.buf_len for f in frames) // 2

    amp_sum = [0.0] * max_words
    zero_cnt = [0] * max_words
    word_n = [0] * max_words
    for f in frames:
        n = f.buf_len // 2
        b = f.buf
        for w in range(n):
            im = b[2 * w] - 256 if b[2 * w] > 127 else b[2 * w]
            re = b[2 * w + 1] - 256 if b[2 * w + 1] > 127 else b[2 * w + 1]
            amp_sum[w] += abs(im) + abs(re)
            word_n[w] += 1
            if im == 0 and re == 0:
                zero_cnt[w] += 1

    mean_amp = [amp_sum[w] / word_n[w] if word_n[w] else 0.0 for w in range(max_words)]
    zero_rate = [zero_cnt[w] / word_n[w] if word_n[w] else 1.0 for w in range(max_words)]

    print(f"\nframes={len(frames)} buf_len={dict(buf_lens)} sig_mode={dict(sig_modes)} "
          f"first_word_invalid={fwi}/{len(frames)}")
    print("\nword  mean_amp  zero%   word  mean_amp  zero%")
    for w in range(0, max_words, 2):
        cols = []
        for ww in (w, w + 1):
            if ww < max_words:
                cols.append(f"{ww:4d}  {mean_amp[ww]:8.2f}  {zero_rate[ww]*100:5.1f}%")
        print("   ".join(cols))

    # HT-LTF range (word 64..127 from frames with len>=256) top 56 by amplitude proposal
    proposal = None
    if max_words >= 128:
        ht = sorted(range(64, 128), key=lambda w: -mean_amp[w])[:56]
        proposal = sorted(ht)
        provisional = sorted(SC_WORD_HTLTF)
        print(f"\n[Proposal] HT-LTF range top 56 words by amplitude:\n  {proposal}")
        if proposal == provisional:
            print("-> Matches firmware provisional table (SC_WORD_HTLTF) -- sc_table.h can be confirmed")
        else:
            only_fw = sorted(set(provisional) - set(proposal))
            only_meas = sorted(set(proposal) - set(provisional))
            print(f"-> Mismatch! Words only in table: {only_fw} / only in measurement: {only_meas}")
            print("  Possible edge behavior (ltf_merge_en) -- check design Section 4.3 note ①")

    Path(args.out).write_text(json.dumps({
        "frames": len(frames),
        "buf_len_hist": dict(buf_lens),
        "sig_mode_hist": {str(k): v for k, v in sig_modes.items()},
        "first_word_invalid": fwi,
        "mean_amp": mean_amp,
        "zero_rate": zero_rate,
        "proposal_words": proposal,
        "provisional_table": SC_WORD_HTLTF,
    }, indent=1))
    print(f"\nSaved: {args.out} -> when confirmed, update configs/rf.yaml sc_table: confirmed + sc_table.h")


if __name__ == "__main__":
    main()
