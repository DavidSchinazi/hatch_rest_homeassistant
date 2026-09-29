# Hatch Rest (1st generation) BLE protocol

What this integration knows about talking to a Hatch Rest over Bluetooth, and — more to the
point — **how much of it we have actually seen happen**.

The reverse engineering is not ours. It was done by [wmbest2][wmbest2] from btsnoop captures and
published as a `PROTOCOL.md` there; the GATT layout is corroborated independently by
[dgreif/homebridge-hatch-baby-rest][dgreif]. What this document adds is verification: every claim
below is marked with whether we have confirmed it against real hardware, and how.

That distinction matters because the two published sources agree with each other but neither
states what was tested on what. Three of the claims we inherited turned out to be wrong for our
devices.

[wmbest2]: https://github.com/wmbest2/hatch_rest_homeassistant/blob/main/PROTOCOL.md
[dgreif]: https://github.com/dgreif/homebridge-hatch-baby-rest

## What "confirmed" means here

Four Hatch Rest 1st generation units, reached through two ESPHome Bluetooth proxies, running
continuously for about four days. Firmware versions were not recorded — the integration never
reads the Device Information service, so we cannot say which they were.

| Marker | Meaning |
|---|---|
| **Confirmed** | Observed on our four devices. The evidence is stated. |
| **Inherited** | Taken from the sources above. Plausible, never exercised here. |
| **Contradicted** | Our devices disagree with the published sources. |

Only the 1st generation Rest is in scope. Rest+, Rest Mini and Rest 2nd gen are Wi-Fi/cloud
devices sharing none of this.

## Transport

**Confirmed.** All three characteristics are live on all four devices.

| Role | UUID | Properties |
|---|---|---|
| Commands (TX) | `02240002-5efd-47eb-9c1a-de53f7a2b232` | write |
| Current state | `02260002-5efd-47eb-9c1a-de53f7a2b232` | read, notify |
| Command replies | `02240003-5efd-47eb-9c1a-de53f7a2b232` | notify |

Commands are ASCII written to TX: a two-letter opcode followed by lowercase hex bytes, `%02x`
each. Replies to them arrive on the third characteristic, never on the state one.

State also reaches us without a connection at all, in the manufacturer-specific advertisement
under manufacturer ID **1076**. Note that it rides in the *scan response*, so only an active scan
collects it.

### Write with response — **Contradicted**

Both sources write without response, and wmbest2's notes state the device does not support
write-with-response on TX. Ours do. Every command this integration has ever sent used
`response=True`: 43 commands over four days with zero failures, plus the entire favorite
machinery since. If your device refuses, this is the first thing to change.

## State payload

**Confirmed** — this predates the favorites work and has been parsed continuously for days,
1,186,242 state notifications in one four-day capture alone.

The same blocks appear in the feedback characteristic and in the advertisement, at different
offsets. Each block is introduced by an ASCII marker byte.

```
feedback        T  ..  ..  ..  ..  C  r  g  b  br  S  sn  vol  P  pwr
                0                  5             9  10         13 14

advertisement   R  T  ..  ..  ..  ..  C  r  g  b  br  S  sn  vol  E  ..×5  P  pwr
                0  1                  6             10  11         14      20 21
```

| Marker | Byte | Follows with |
|---|---|---|
| `C` | `0x43` | red, green, blue, brightness |
| `S` | `0x53` | sound id, volume |
| `P` | `0x50` | the power byte |
| `E` | `0x45` | five bytes, **Inherited** — purpose unknown, and zero in the one raw advertisement we captured |

Captured from one of our devices, both decoding to the same state:

```
feedback       54f8001c9643fdd12d7f53055450df6500000000
advertisement  5254f8001ccc43fdd12d7f53055445000000000050df6500
               -> color (253, 209, 45), brightness 127, ocean, volume 84, off, no favorite
```

Colour here is **red first**. (The favorite block is not — see below.)

### The power byte

**Confirmed**, seven independent cross-checks.

```
bit 7,6   set while the device is off
bits 5..0 the favorite currently playing, 1-6; 0, 0x1f and 0x3f all mean none
```

So `power = not (byte & 0xC0)` and `favorite = byte & 0x3F`, treating anything outside 1–6 as no
selection. Observed values include `0x00`, `0x01`–`0x06`, `0x80`, `0xC0` and `0xDF`.

The evidence: cycling one device through its favorites on the touch ring, the live colour,
brightness, sound and volume matched the stored contents of whichever slot the low bits named,
every time — across four favorites on one device and one more on each of the other three. See
[Favorites](#favorites) for what "stored contents" means.

dgreif's code names this same field `powerPreset`, which is what put us onto it.

## Commands that change what is playing

**Confirmed** — these are the integration's original four, exercised constantly.

| Command | Effect |
|---|---|
| `SI{01\|00}` | power on / off |
| `SC{rr}{gg}{bb}{ll}` | colour and brightness |
| `SN{nn}` | sound |
| `SV{vv}` | volume |

Setting the colour to `(254, 254, 254)` reportedly switches the device into gradient mode. That
claim is **Inherited** from upstream; we have seen that exact colour stored in a favorite on three
of our four devices, which is suggestive, but we never verified the behaviour.

### Sound ids

**Partly confirmed.** The numbering has gaps — 1, 8 and 12 are absent — which is why this is a
lookup rather than a range.

| id | sound | | id | sound |
|---|---|---|---|---|
| 0 | none *(seen)* | | 7 | rain *(seen)* |
| 2 | stream *(seen)* | | 9 | bird *(seen)* |
| 3 | noise *(seen)* | | 10 | crickets |
| 4 | dryer | | 11 | brahms |
| 5 | ocean *(seen)* | | 13 | twinkle |
| 6 | wind | | 14 | rockabye |

*(seen)* marks ids we have actually observed in live state or in a stored favorite. The rest are
inherited from [kjoconnor/pyhatchbabyrest][kjoconnor] and unverified.

[kjoconnor]: https://github.com/kjoconnor/pyhatchbabyrest

## Every command is acknowledged

**Confirmed**, and **Contradicted** in the detail.

The device answers *every* command written to TX with ASCII `OK` (`4f4b`) on the reply
characteristic, sent after whatever data the command asked for. In one session: 40 commands, 40
acknowledgements, no exceptions — including plain state commands like `SI00`, not just the
favorite ones.

wmbest2's notes describe the commit command specifically as replying `"01"`, and suggest
distinguishing that from a query reply by tracking which was outstanding. We never saw `01`. We
see `OK` for everything, which is simpler: it marks the end of an exchange, so waiting for it is
what makes it safe to send the next command.

`SiloCityLabs/hatch-rest-gen1` independently mentions an `OK` / `E01`–`E06` handshake, which fits
what we see, though we have never observed an error reply.

## The sleep timer

`SD{ssss}` sets it, in **seconds**, four hex digits. `GI` asks whether one is running and `GD` how
many minutes are left — note that setting and reporting use different units.

**Confirmed**: an idle device answers `GI` with `FF` on three of our four devices and `00` on the
fourth, which the notes do not mention. Both mean the same thing.

**Unconfirmed**: what `GI` answers while a timer *is* running. We have never seen one, so the
integration logs that reply as it arrives rather than interpreting it, and asks `GD` for the
figure it actually uses.

## Favorites

Six slots, numbered 1–6, stored on the device. **Confirmed**: all six read on all four devices.

### Reading a slot

`PGB{NN}` — note this one takes **uppercase** hex in the published source; our devices accept it
and we kept it that way. The reply is a 15-byte block on the reply characteristic.

```
[0x01] [sound] [volume] [00 ×6] [brightness] [B] [G] [R] [flags] [0x03]
   0      1        2      3-8        9        10  11  12    13     14
```

**The colour is blue first here** — the reverse of both the state payload and the command that
writes a favorite. This is the single easiest thing to get wrong, and it fails silently by
swapping red and blue.

**Confirmed** by favorite 2, which holds `(253, 209, 45)` on all four devices: decoded that way it
matches the live state exactly whenever the device reports favorite 2 as active. Decoded the other
way it would read `(45, 209, 253)`. Most of the other default slots are greys, which prove
nothing either way.

`flags & 0x80` is whether the device offers the slot when cycling favorites on the touch ring.
**Confirmed** end to end: enabling slot 5 from Home Assistant made it appear in the physical
rotation.

### Writing a slot

Six commands in order. Nothing is stored until the last one, which commits every field at once.

```
PSB{NN}                  select the slot to write
PSC{rr}{gg}{bb}{ll}      colour and brightness   <- red first
PSN{nn}                  sound id
PSV{vv}                  volume
PSL{ff}                  flags: c0 enabled, 80 disabled
PSF                      commit
```

**Confirmed**: four writes across two slots, each verified by reading the slot back afterwards
and comparing every field against what was asked for.

Because the commit writes all five fields, a partial sequence does not leave the others alone — it
leaves them at whatever the device collected. Anything you do not intend to change has to be read
first and sent back unchanged. Our one-field change (flipping `enabled` on slot 5) demonstrates
this working: the read-back differed from the previous read in exactly that field.

If a command goes unacknowledged, stop rather than continue. Nothing is committed until `PSF`, so
abandoning the sequence leaves the slot untouched — whereas carrying on could commit to whichever
slot the device still had selected if it was `PSB` that went missing.

### Selecting a slot

`SP{NN}` plays favorite N; `SP00` deselects. **Confirmed.**

Two things we established that the sources do not mention:

- **Storing a favorite does not select it.** The device goes on reporting whichever was playing
  before, or none at all — and none is the usual case, since adjusting the light or sound by hand
  is what clears the selection.
- **`SP` does not power on a device that is off.** Selecting a favorite on a device that was off
  moved the reported favorite from 1 to 6 with the power bits unchanged.

### Names — **Inherited, and unobserved**

wmbest2's implementation parses a notification headed `0x07` followed by ASCII as a slot's name.
We have never seen one: 24 slot reads across four devices produced zero name notifications. In
that fork's own code the parsed name is filed against schedules rather than favorites, so it may
not be wired for favorites at all.

We keep the parsing in case some device sends it, and fall back to numbering the slots.

## The clock

`ST{YYYYMMDDHHmmss}U`, followed by the usual `OK`. **Confirmed** only as far as the
acknowledgement goes: there is no command to read the clock back, so nothing can check what the
device did with it beyond watching whether schedules fire on time.

The device is told the **local wall clock with no zone**, which is the same convention its
schedules use for their start times. Converting to UTC first would be converting to nothing.

We send it at most once a day, when a device connects, and never between midnight and 02:30. In
that window the local clock is ambiguous on the day the clocks go back and absent on the day they
go forward, so a time sent then can be an hour out. jmnatzaganian's fork does the same, which is
where the idea came from.

## Not investigated

Present in the published sources, untouched here, and therefore entirely **Inherited**:

- `GF` — query the active favorite. We never send it; the power byte already carries the answer.
- Writing schedules — `ESB`/`ESL`/`ESF` toggle one on or off, but nothing documents how to set a
  schedule's time, sound, colour or days. jmnatzaganian's fork only toggles them too.

## Schedules

**Confirmed**: ten slots, read with `EGB{NN}` (`01`–`0A`), replying with a 20-byte block on
`CHAR_LIST`. All forty slots across four devices read without a timeout.

The block shares its `0x01` header with a favorite and differs only in length, 20 against 15.
A schedule fed to the favorite parser does not fail — it returns a plausible favorite assembled
from the wrong bytes — so the only safe way to tell them apart is which one was asked for.

```
[0x01] [start LE ×4] [sound] [volume] [duration LE ×2] [00 ×4] [brightness] [B] [G] [R] [00] [days] [flags]
   0         1-4         5        6          7-8          9-12       13      14  15  16   17    18     19
```

**Confirmed**: sound, volume, brightness, colour (blue first, as in a favorite), and days. Days is
a bitmask with bit 0 = Sunday; real slots read 0x3e for weekdays, 0x60 for the weekend, 0x00 for
an empty slot. Two things carried it: an "Ok to Wake" schedule decodes green, and a schedule its
owner had named "Tuesday Morning" has its bitmask set to exactly 0x04.

### The start time and duration — **Contradicted**

Both sources call bytes 1-4 a modified timestamp and put the hour at byte 7 and the minute at byte
8. Read from real slots that gives 46:14, 238:182 and 254:196, and the disabled slots all read
46:14, which is simply the unset value.

Bytes 1-4 are the **start time**: a unix timestamp whose time of day, read **as UTC**, is the local
time the schedule runs at. The device writes local wall clock into a timestamp-shaped field with no
zone, so reading it as UTC rather than converting is what makes it right — and what makes it immune
to daylight saving. The date half is when the slot was last written.

Bytes 7-8 are the **duration in seconds**, little endian.

Confirmed across 21 populated slots on four devices, every one of which then reads sensibly against
the name its owner gave it:

```
Weekday Wakeup    07:00  for 1h00m   Mon,Wed,Thu,Fri
Tuesday Morning   06:45  for 1h00m   Tue
Playtime Week     08:00  for 0h10m   Mon-Fri
Nap Time          13:00  for 2h00m   every day
Bed Time          19:00  for 12h00m  every day
Weekend Sleep     19:00  for 12h30m  Fri,Sat
```

Every duration seen ends in a spare 30 seconds, which is presumably how the app writes them.

`flags & 0x40` is enabled — **confirmed**, and worth stating because the notes give `0x80` for a
favorite and `0x40` for a schedule while both are written as `0xc0`, so the write side cannot tell
them apart. Populated slots read `0xdf` and an empty one `0x9f`: `0x80` is set in both, so it
cannot be the enabled bit, and `0x40` can.

**Contradicted**: the notes put the hour at byte 7 and the minute at byte 8. Real slots give 46:14,
238:182 and 254:196 there. Those bytes are zero on empty slots, so they are schedule data of some
kind, but they are not the time of day. Where the device actually keeps it is **unknown** —
possibly among bytes 9–12, which the notes call padding and nobody has looked at.

### Names

**Contradicted.** The notes describe a name notification headed `0x07`. Schedules on our devices
send theirs headed `0x04`, `0x05` or `0x85` — "Ok to Wake", "Nap Time", "Bed Time", "Weekday
Sleep", "Weekend Wakeup". So the leading byte is not what identifies one; the printable text after
it is. Favorites still send no name at all.

A second fork, `SiloCityLabs/hatch-rest-gen1`, implements schedules with a different and larger
command set. Where it overlaps wmbest2 it agrees; where it does not, its provenance is harder to
check — its README links to reverse-engineering notes that were never committed.

## Summary of disagreements with the published sources

| Claim | Source says | Our devices |
|---|---|---|
| Write with response on TX | unsupported | works, thousands of commands |
| Commit reply | `01` | `OK`, same as every other command |
| Favorite names | sent as `0x07` blocks | never sent, 24 reads |
| Schedule names | sent as `0x07` blocks | sent headed `0x04`, `0x05` or `0x85` |
| Schedule hour/minute | bytes 7 and 8 | those hold something else; time not located |
| Idle sleep timer | `GI` answers `FF` | three devices say `FF`, one says `00` |
| Schedule bytes 1-4 | a modified timestamp | the start time, read as UTC |
| Schedule bytes 7-8 | the hour and minute | the duration, in seconds |
