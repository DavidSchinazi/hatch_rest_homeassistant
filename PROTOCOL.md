# Hatch Rest (1st generation) BLE protocol

What this integration knows about talking to a Hatch Rest over Bluetooth, and which aspects of
that have been confirmed by testing locally with hardware.

The initial reverse engineering was done by wmbest2 from btsnoop captures and published as a
[`PROTOCOL.md`](https://github.com/wmbest2/hatch_rest_homeassistant/blob/main/PROTOCOL.md).
The GATT layout was corroborated independently by
[dgreif/homebridge-hatch-baby-rest](https://github.com/dgreif/homebridge-hatch-baby-rest).
What this document adds is verification: every claim below is marked with whether we have
confirmed it against real hardware, because some of the data in these two published sources
did not seem to work with our Hatch devices.

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

`GI` asks whether a timer is running and `GD` how many minutes are left.

**`SD{ssss}` does not work — Contradicted.** Both sources document it as setting the timer in
seconds, four hex digits, and neither says it was tested. On our devices it is accepted and then
ignored:

- Four attempts, two devices: `SD00b4`, `SD00b4`, `SD0960`, `SD01e0`.
- Every one was acknowledged with `OK`.
- `GI` answered `FF` immediately afterwards each time — no timer running.
- Neither device switched off when its timer should have elapsed, with the light on and sound
  playing throughout.

`SD0960` contains no hex letters, so lowercase digits are not the problem. What is wrong — the
unit, the framing, a terminator like the one `ST` carries, or the command itself — is **unknown**.
Reading the timer works; setting it is not implemented here until there is something that does.

**Confirmed**: an idle device answers `GI` with `FF` on three of our four devices and `00` on the
fourth, which the notes do not mention. Both mean the same thing.

**Unconfirmed**: what `GI` answers while a timer *is* running. We have never managed to start one,
so the integration logs that reply as it arrives rather than interpreting it, and asks `GD` for the
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
that fork's own code the parsed name is filed against programs rather than favorites, so it may
not be wired for favorites at all.

We keep the parsing in case some device sends it, and fall back to numbering the slots.

## The clock

`ST{YYYYMMDDHHmmss}U`, followed by the usual `OK`. **Confirmed** only as far as the
acknowledgement goes: there is no command to read the clock back, so nothing can check what the
device did with it beyond watching whether programs fire on time.

The device is told the **local wall clock with no zone**, which is the same convention its
programs use for their start times. Converting to UTC first would be converting to nothing.

We send it at most once a day, when a device connects, and never between midnight and 02:30. In
that window the local clock is ambiguous on the day the clocks go back and absent on the day they
go forward, so a time sent then can be an hour out. jmnatzaganian's fork does the same, which is
where the idea came from.

## Not investigated

Present in the published sources, untouched here, and therefore entirely **Inherited**:

- `GF` — query the active favorite. We never send it; the power byte already carries the answer.
- Writing programs — nothing documents how to set a program's time, sound, colour or days.
  jmnatzaganian's fork only toggles them, as this integration now does (see
  [Enabling and disabling](#enabling-and-disabling--inherited)).

## Programs

**Confirmed**: ten slots, read with `EGB{NN}` (`01`–`0A`), replying with a 20-byte block on
`CHAR_LIST`. All forty slots across four devices read without a timeout.

The block shares its `0x01` header with a favorite and differs only in length, 20 against 15.
A program fed to the favorite parser does not fail — it returns a plausible favorite assembled
from the wrong bytes — so the only safe way to tell them apart is which one was asked for.

```
[0x01] [start LE ×4] [sound] [volume] [duration LE ×2] [00 ×2] [lock LE ×2] [brightness] [B] [G] [R] [??] [days] [flags]
   0         1-4         5        6          7-8          9-10       11-12         13      14  15  16   17    18     19
```

**Confirmed**: sound, volume, brightness, colour (blue first, as in a favorite), and days. Days is
a bitmask with bit 0 = Sunday; real slots read 0x3e for weekdays, 0x60 for the weekend, 0x00 for
an empty slot. Two things carried it: an "Ok to Wake" program decodes green, and a program its
owner had named "Tuesday Morning" has its bitmask set to exactly 0x04.

### The start time and duration — **Contradicted**

Both sources call bytes 1-4 a modified timestamp and put the hour at byte 7 and the minute at byte
8. Read from real slots that gives 46:14, 238:182 and 254:196, and the disabled slots all read
46:14, which is simply the unset value.

Bytes 1-4 are the **start time**: a unix timestamp whose time of day, read **as UTC**, is the local
time the program runs at. The device writes local wall clock into a timestamp-shaped field with no
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

### Toddler Lock — not in either source

Bytes 11-12, zero when off and `0x01ff` when on. Neither published source mentions this field at
all; both treat bytes 9-12 as padding.

Found by diffing all forty slots across a session in which the owner turned the toggle on in the
Hatch app. Exactly one slot changed, and apart from the date half of the start value the only
difference was those two bytes going from `0000` to `ff01`. The program's run time was unchanged
either side, which re-confirms the start-time reading at the same time.

**Confirmed** a second time by two programs created in the app on the same device, identical but
for the toggle: the one with Toddler Lock on reads `ff01` there, the one without `0000`.

`0x01ff` is a strange thing to store for something the app presents as a switch, so the integration
reports only whether it is set and keeps the whole block for whatever the value may otherwise mean.

### The date half of the start value — **unknown**

Its time of day is the run time, which is confirmed. What the date is for is not. On the slot that
was edited it moved from 2020-01-20 to 2020-01-21 — forward by exactly one day, six years in the
past, rather than to the date of the edit. So it is not a last-written date, whatever else it is.

### Whether a program is enabled — **Contradicted**

Not in the block at all. The notes give `flags & 0x40`, but every populated slot on all four devices
reads `0xdf` there, enabled or not.

Every `EGB` reply is followed by a second, 17-byte notification: a **status byte**, then the name
as NUL-padded ASCII (see [Names](#names)). Bit `0x02` of the status byte is enabled. **Confirmed**
against the app on one device, all six populated slots:

```
Time to Rise      06  enabled         Nap Time          04  disabled
TestA             07  enabled         Bed Time          05  disabled
TestB             07  enabled         Wake up Weekend   05  disabled
```

and by both directions of writing it: `ESLc0` from Home Assistant took Nap Time from `0x04` to
`0x06`, and disabling TestA and TestB in the app took both from `0x07` to `0x05`. Before either,
`ESL40` sent to an empty slot had moved its status from `0x00` to exactly `0x02`. The
other bits are unknown; `0x04` is set on every populated slot seen, and `0x85` turned up once in
older captures.

A red herring worth recording: byte 17 of the block moved from `0xff` to `0x00` when the app enabled
"Time to Rise", and read `0x00` on both TestA and TestB — but also on Nap Time while it was
disabled, and it stayed `0x00` on TestA and TestB after the app disabled them. What it means is
unknown.

An empty slot is all zeros in both replies.

### Enabling and disabling — **Confirmed**

```
ESB{NN}     select the slot to write, uppercase hex as EGB takes it
ESL{ff}     c0 to enable, 80 to disable
ESF         commit; answered with the slot number in ASCII ("04"), then OK
```

`ESLc0` enabled Nap Time on a real device: the app showed it enabled afterwards, and the status
byte read back `0x06` where it had been `0x04`. `ESL`'s `0x40` lands as `0x02` of the status byte.

`ESF` commits only that. The 20-byte block read back identical, so unlike a favorite's `PSF` it does
not rewrite fields it was not sent. The slot is still read back after every write, and anything
other than the status that moved is logged as a warning.

Disabling from Home Assistant (`ESL80`) has not been exercised on a populated program yet.

### Names

**Contradicted.** The notes describe a name notification headed `0x07`. Programs on our devices
send theirs headed anywhere from `0x02` to `0x07`, or `0x85` — "Ok to Wake", "Nap Time", "Bed
Time", "Weekday Sleep", "Weekend Wakeup". So the leading byte is not what identifies one; the
printable text after it is. Favorites still send no name at all. An empty slot answers with 17
zero bytes.

The leading byte is the program's status, `0x02` of which is whether it is enabled — see
[Whether a program is enabled](#whether-a-program-is-enabled--contradicted). Bytes after the name's
NUL are not always zero ("Bed Time" is followed by `ff81008000ffff`, and TestA and TestB gained a
`0x65` there when the app disabled them), and are not understood.

A second fork, `SiloCityLabs/hatch-rest-gen1`, implements programs with a different and larger
command set. Where it overlaps wmbest2 it agrees; where it does not, its provenance is harder to
check — its README links to reverse-engineering notes that were never committed.

## Summary of disagreements with the published sources

| Claim | Source says | Our devices |
|---|---|---|
| Write with response on TX | unsupported | works, thousands of commands |
| Commit reply | `01` | `OK`, same as every other command |
| Favorite names | sent as `0x07` blocks | never sent, 24 reads |
| Program names | sent as `0x07` blocks | sent headed `0x02`–`0x07` or `0x85` |
| Program enabled | `flags & 0x40` | `0x02` of the status byte ahead of the name; flags always `0xdf` |
| Program hour/minute | bytes 7 and 8 | those hold something else; time not located |
| Idle sleep timer | `GI` answers `FF` | three devices say `FF`, one says `00` |
| Program bytes 1-4 | a modified timestamp | the start time, read as UTC |
| Program bytes 7-8 | the hour and minute | the duration, in seconds |
| Program bytes 11-12 | padding | the app's Toddler Lock |
| `SD` sets the sleep timer | in seconds, four hex digits | acknowledged and ignored |
