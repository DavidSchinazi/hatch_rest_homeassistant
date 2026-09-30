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

Commands are ASCII written to TX: a two- or three-letter opcode followed by hex. The case matters
for at least one command, and is not consistent across them: the state commands (`SI`, `SC`, `SN`,
`SV`) and the favorite writes (`PS*`) are sent in lowercase and work; `PGB`, `EGB`, `ESB` and `SD`
are sent in uppercase and work; and `SD` in lowercase did not. Replies arrive on the third
characteristic, never on the state one.

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
| `T` | `0x54` | four bytes, the device's clock — see below |
| `C` | `0x43` | red, green, blue, brightness |
| `S` | `0x53` | sound id, volume |
| `P` | `0x50` | the power byte |
| `E` | `0x45` | five bytes, **Inherited** — purpose unknown, and zero in every raw advertisement we captured |
| `e` | `0x65` | in the feedback, four bytes after the power byte; in the advertisement, one byte. The first is `0x80` while a sleep timer runs and `0x00` otherwise, **confirmed** — see [The sleep timer](#the-sleep-timer) |

The `T` block is the device's clock, **confirmed**: a big-endian unix timestamp holding local wall
clock read as UTC, the same convention as a program's start time. One advertisement logged at
12:57:58 local carried `6abbb5d6`, which is 12:57:58 read that way. Devices whose clock has not
been set carry small or meaningless values.

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

Setting the colour to `(254, 254, 254)` switches the device into gradient mode, cycling through
colours on its own. **Confirmed** by watching a device do it. The same colour is stored in favorite
6 on three of our four devices, which is presumably how the app saves a gradient favorite.

### Sound ids

**Confirmed.** The numbering has gaps — 1, 8 and 12 are absent — which is why this is a lookup
rather than a range.

| id | sound | | id | sound |
|---|---|---|---|---|
| 0 | none | | 7 | rain |
| 2 | stream | | 9 | bird |
| 3 | noise | | 10 | crickets |
| 4 | dryer | | 11 | brahms |
| 5 | ocean | | 13 | twinkle |
| 6 | wind | | 14 | rockabye |

The names are from [kjoconnor/pyhatchbabyrest][kjoconnor]. Every id was played on one device from
Home Assistant, `SN02` through `SN0e` and back to `SN00`, and each was reported straight back in the
state payload within about a second, while someone listened.

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

`SiloCityLabs/hatch-rest-gen1` independently mentions an `OK` / `E01`–`E06` handshake. **`E01` is
confirmed**: it came back in place of `OK` for `EST` given a value of the wrong length or format
(`EST0730`, `EST073000`, `EST260930073000`, `EST20260930073000U`, `EST68F7BC6A`). What `E02`–`E06`
mean, if they exist, is unknown.

## The sleep timer

### Reading it — `GD` is seconds, **Contradicted**

`GD` answers with the time left in **seconds**, four ASCII hex digits — not the minutes the notes
give. **Confirmed** with a 3h01m (10860 s) timer started from the app: about five minutes later
the device answered `2932`, which is 10546 seconds, 175.8 minutes. Read as minutes that would be a
week. Idle devices answer `0000`.

`GI`, which the notes call the total, is **unknown**. The same device answered `FF` — the notes'
"no timer" — with that timer running, and idle devices answer `FF` or `00`. It decides nothing;
the integration logs it and uses `GD` alone.

A running timer also shows in the state payload, **confirmed**: the first byte of the `e` block
after the power byte is `0x80` while a timer runs and `0x00` otherwise. Seen on two devices each
with a timer running, in the feedback (`65 80000000`) and in the advertisement (`e` then `0x80`),
against `0x00` on every idle device, and going back to `0x00` the moment a timer ran out. The
integration does not use it yet — `GD` gives the time left, which this does not. What the other
three bytes in the feedback carry is **unknown**; they have always been zero.

### Setting it — `SD{SSSS}`, **Confirmed**

`SD` takes the time in **seconds**, four **uppercase** hex digits, and `SD0000` cancels. It is
acknowledged with `OK`, like every command.

**Confirmed** end to end: `SD003C` sent to a device that was playing, with light on. `GD` answered
`003C` straight afterwards, the `e` block after the power byte read `0x80` while it ran, and the
device switched itself off 61 seconds after the command was sent — the power byte went
to off, and `e` back to `0x00`. It replaced a timer the app had set, rather than adding to it.

Cancelling is **confirmed** too: `SD0000`, sent to a device with an app-set timer about 8h24m from
running out, was followed by `GD` answering `0000`. A 15-minute timer set right after it, `SD0384`,
read back `0383` — 899 seconds, one already spent.

Four earlier attempts had looked ignored: `SD00b4`, `SD00b4`, `SD0960`, `SD01e0`, all acknowledged,
with neither device switching off. They were lowercase — though `SD0960` has no hex letters — and
were judged partly by `GI` answering `FF` straight afterwards, which says nothing about the timer.
Why they did not run is **unknown**; uppercase is what is known to work.

Four hex digits of seconds hold up to 18h12m; the app offers at least nine hours.

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

`ST{YYYYMMDDHHmmss}U`, followed by the usual `OK`. **Confirmed**: there is no command to read the
clock back, but the `T` block of the state payload carries it. On two devices, the first `T` after
`ST20260929181202U` read 18:12:02 and the first after `ST20260929194303U` read 19:43:04.

The device is told the **local wall clock with no zone**, which is the same convention its
programs use for their start times. Converting to UTC first would be converting to nothing.

We send it at most once a day, when a device connects, and never between midnight and 02:30. In
that window the local clock is ambiguous on the day the clocks go back and absent on the day they
go forward, so a time sent then can be an hour out. jmnatzaganian's fork does the same, which is
where the idea came from.

## Not investigated

Present in the published sources, untouched here, and therefore entirely **Inherited**:

- `GF` — query the active favorite. We never send it; the power byte already carries the answer.
- `EGP`, which `SiloCityLabs/hatch-rest-gen1` reads as a program's "presets". It answered `00` on
  every slot read.

## Programs

**Confirmed**: ten slots, read with `EGB{NN}` (`01`–`0A`), replying with a 20-byte block on
`CHAR_LIST`. All forty slots across four devices read without a timeout.

The block shares its `0x01` header with a favorite and differs only in length, 20 against 15.
A program fed to the favorite parser does not fail — it returns a plausible favorite assembled
from the wrong bytes — so the only safe way to tell them apart is which one was asked for.

```
[0x01] [start LE ×4] [sound] [volume] [duration LE ×2] [00 ×2] [lock LE ×2] [brightness] [B] [G] [R] [??] [days] [written]
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
to daylight saving. The date half is whatever date the writer sent; see below.

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

### The date half of the start value

It is the date part of whatever `EST` was sent: `EST20260930073000` read back as
`2026-09-30 07:30:00`. The app sends dates years in the past — 2017 to 2020 on our devices, and on
one edit it moved forward by exactly one day — so it is not the date of the edit either. Whether the
device does anything with the date at all is **unknown**. Programs written with the current date
showed correctly in the app.

### The last byte: which fields the last save wrote — **Contradicted**

The notes read byte 19 as flags, `0x40` enabled. It is a record of which fields the last `ESF`
saved, one bit per field command. **Confirmed** by saving one field at a time into a slot and
watching it change:

```
0x80  ESW days      0x10  ESC colour and brightness      0x02  ESN sound
0x40  ESI           0x08  ESD duration                   0x04  ESV volume
0x01  set on most saves; which command owns it is unclear
```

`0x20` was never set by any single command, and is the one bit missing from `0xdf`, which is what
every program the app saves reads: all fields but one.

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
`ESL80` disables, confirmed the same way: Time to Rise went from `0x06` to `0x04`, and the app
agreed.

A save of `ESL` alone changes only the status: both times, the 20-byte block read back identical.
That is special to `ESL` — see the next section, where every other save rewrites the whole
program.

### Writing a program — **Confirmed**

A program is written as a complete sequence:

```
ESB{NN}                 select the slot, uppercase hex
EST{YYYYMMDDHHMMSS}     start: local wall clock, no zone; only the time of day is known to matter
ESD{SSSS}               duration in seconds, four hex digits
ESW{DD}                 days, bit 0 = Sunday
ESI{II}                 01 on every program the app wrote; meaning unconfirmed
ESC{RR}{GG}{BB}{LL}     colour and brightness, red first
ESN{NN}                 sound id
ESV{VV}                 volume
ESX{name}               the name, as plain ASCII
ESM{LLLL}0000           Toddler Lock: 0000 off, 01FF on
ESL{FF}                 85 on every program the app wrote; 0x40 added enables it
ESF                     commit; answered with the slot number in ASCII, then OK
```

**Confirmed** by writing two programs this way on one device, every field different between them,
and reading each back both over Bluetooth and in the app, which showed exactly what was written:

```
TestA   21:15  1h00m  Mon,Wed,Fri  red at 25%     ocean, volume 48  Toddler Lock on   enabled
TestB   07:30  0h10m  every day    white at 50%   rain, volume 32   Toddler Lock off  enabled
```

Then again through the integration's own editor, rewriting TestB with every field changed, a name
containing a space, and the `ESI` and `ESL` values the app writes (`01`, and `85` with `0x40` clear
to disable it). Read back over Bluetooth and in the app, it matched:

```
Test B  06:45  1h30m  Tue,Thu      green at 200   bird, volume 100  Toddler Lock on   disabled
```

So a name can contain spaces, sent as they are after `ESX`, and `EGL` read back `85` — a program
written this way looks to the device like one the app wrote.

**There is one staging buffer for all programs, and `ESF` saves all of it.** `ESB` does not load
the selected slot into it. Saving a single field — `ESB05`, `ESD0258`, `ESF` — wrote the new
duration, and with it a sound, volume, colour, days and a mangled name ("\0ap Time") left over
from an earlier write to a different slot, with the start time zeroed. So the only safe write is a
complete one, with every field sent, as for a favorite. To change one field, read the others first
and send them back unchanged.

`EST` and `ESX` are acknowledged on their own but store nothing unless they are part of such a
complete write, which is why neither seemed to work when tried alone.

Each field also has a getter taking the slot, answering in the same encoding the setter takes:
`EGT` (`20200118130000`), `EGD`, `EGW`, `EGC`, `EGN`, `EGV`, `EGX` (the name as text), `EGM`
(`01FF0000`), `EGI`, `EGL` and `EGP`. **Confirmed** by reading back both test programs.

What `ESI`/`EGI` and `ESL`/`EGL` mean beyond enabling is **unknown**. `SiloCityLabs/hatch-rest-gen1`
reads `EGI` as "power" and `EGL`'s bits as `0x80` exists, `0x40` enabled, `0x10` sleep timer, `0x08`
light off, `0x04` light on, `0x02` sound off and `0x01` sound on — which would make `85` "exists,
light on, sound on". But the app never sets `0x40` on programs it shows enabled, so that reading is
at least partly wrong. Writing `ESI00` also cleared the Toddler Lock bytes, so `ESI` and `ESM` are
related in some way not understood.

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
| `GI` | the timer total; `FF` is no timer | `FF` with a timer running; not understood |
| `GD` | minutes remaining | seconds remaining |
| Program bytes 1-4 | a modified timestamp | the start time, read as UTC |
| Program bytes 7-8 | the hour and minute | the duration, in seconds |
| Program bytes 11-12 | padding | the app's Toddler Lock |
| Program byte 19 | flags, `0x40` enabled | which fields the last save wrote |
| Saving one program field | — | saves a staging buffer shared by every slot; write all fields |
| `SD` sets the sleep timer | in seconds, four hex digits | works, in uppercase; lowercase attempts did not run |
