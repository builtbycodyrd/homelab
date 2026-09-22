# Storage and Hardware

## The machine

A dual-socket Supermicro server board with two Xeon E5-2620 v2 processors — 24 threads across two NUMA nodes — and 224 GB of registered ECC DDR3. It's previous-generation enterprise hardware, which is exactly why it makes sense: this class of machine is affordable secondhand, has the memory capacity and PCIe lanes that consumer boards don't, and is built to run continuously.

ECC memory matters here for the same reason it matters in a server room. ZFS checksums data on the way to disk; ECC protects it in the moment before that, while it's still in RAM.

### Diagnosing a bad DIMM

The board wouldn't complete POST after a memory change, halting at a specific diagnostic code. I isolated it by halving the populated banks and retesting, narrowing it to one pair and then to one stick — the slot itself tested fine with a known-good module. The machine runs on the remaining sticks while I source a replacement.

Methodical bisection beats swapping parts hoping something changes. It also tells you which of the two suspects — the module or the slot — actually failed.

### Fitting a GPU into a slot that wasn't built for it

The board's expansion slots are x8 and closed at the back. The transcoding GPU is a physical x16 card.

Electrically this is a non-issue: a x16 card runs fine at x8, negotiating fewer lanes and losing a little bandwidth that a video encoder never uses. The only obstacle was the plastic end-stop on the slot. Cutting a slot open is a known and reversible-ish modification — I checked the pinout, confirmed there were no traces at risk, cut carefully, and seated the card.

Verified afterward:

- The card enumerates on the PCIe bus and reports linking at Gen3 x8, exactly as expected.
- The driver loads and the device nodes come up at boot, via a systemd oneshot rather than a load side effect.
- A transcode runs end to end with the GPU doing the work rather than the CPU.

It's been stable since. The card is a Pascal-generation professional GPU, which constrains the driver: only one NVIDIA branch still supports it, and several point releases in that branch fail to compile against the running kernel. The working version is pinned and installed through DKMS so it rebuilds when the kernel changes.

## Storage design

Three separate ZFS pools:

| Pool | Layout | Holds |
|---|---|---|
| Root | Single SSD | Hypervisor and its configuration |
| Applications | 2-way mirror | Container root filesystems and application data |
| Bulk | Three 2-way mirrors striped | Large media files |

### Why ZFS

Checksums on every block, so silent corruption is detected rather than quietly served. Snapshots before any risky change, taken in seconds and rolled back just as fast. Full visibility into pool health rather than an opaque RAID controller with a blinking light.

### Why mirrors and not RAIDZ

RAIDZ gives more usable capacity per drive, and for a pure archive it would be the better trade. This pool hosts running containers, and that changes the math:

- **Random I/O.** A RAIDZ vdev delivers roughly the IOPS of a single disk. Striped mirrors scale with the number of vdevs. Containers do small scattered reads all day.
- **Resilver time.** Replacing a disk in a mirror copies from its partner. RAIDZ has to read every remaining disk and recompute parity, which takes far longer — and the window while a degraded array is rebuilding is exactly when a second failure hurts.
- **Expansion.** Adding capacity means adding a pair of drives, not rebuilding the pool.

The capacity I gave up is capacity I wasn't using. The latency I kept, I notice every day.

### Other decisions

**ARC is capped.** ZFS will use all available memory for cache by default, which is right on a dedicated storage appliance and wrong on a box that also runs workloads. The cap leaves headroom for the containers.

**Pools are separated by failure domain.** A media pool filling up is an annoyance. A root pool filling up takes the hypervisor down with it. Keeping them apart means the loud, fast-growing data can never starve the thing that keeps everything running.

**No out-of-band management.** The board's IPMI controller caused more problems than it solved and was removed, which means recovery is physical. That's a deliberate trade, and it makes me more careful about changes that could affect boot.
