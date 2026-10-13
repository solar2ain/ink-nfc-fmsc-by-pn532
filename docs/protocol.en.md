# Protocol and Compatibility

[中文](protocol.md) | [English](protocol.en.md)

## Reference Manuals

Good Display provides the following documents. This project links to their download pages rather than redistributing the manual files.

| Manual | Coverage |
|---|---|
| [NFC FMSC ESL User Development Manual](https://www.good-display.com/companyfile/1835.html) | Chapter 4: initialization and PIN access; chapter 5: panel operation sequences; chapter 7: APDU definitions |
| [FMSC NFC Protocol (Chinese)](https://www.good-display.cn/companyfile/1791.html) · [English version](https://www.good-display.com/companyfile/2066.html) | Compact protocol: sections 2.1, 2.2 and 2.3 cover D2, D4 and DE, plus status words. Use the full manual above for driver configuration |
| [FM Security Controller / IC Driver FM1280](https://www.good-display.com/companyfile/1837.html) | Chip interfaces and electrical characteristics; hardware specifications do not replace the command and storage limits of a particular ESL firmware |

Huawei `7A/7D` activation, the known PIN, capacity budgets and A0/A1 segmentation are adaptations for tested boards, not universal FMSC requirements established by these manuals. The 9 Hz poll rate and three-idle confirmation are also tool policies.

## Pipeline

The tool executes a complete stored panel recipe rather than assembling a refresh from separate DC calls.

1. Activate with standard `26/52` or private `7A/7D` 7-bit frames, select the UID and establish ISO-DEP.
2. Read D1 and verify UID, APPID, geometry, scan settings and compression capability. APPID is not a unique hardware-model identifier.
3. Use D8 to check access. Private-wake Huawei boards returning `6985` receive one known-PIN verification and another D8 check; standard-wake mode sends no PIN.
4. When replacing the driver, write DB/DA and verify configuration through D1.
5. Upload with compressed D3 or explicitly select raw D2.
6. D4 executes the stored reset, initialization, RAM transfer, PON, DRF, BUSY and POF operations defined by the recipe.
7. Poll DE at a default target of 9 Hz. After observing BUSY, finish waiting on three consecutive idle samples. If BUSY was never observed, a full-frame operation retains the minimum observation duration and reports uncertainty. A segmented pass must show BUSY before advancing.

## Main Commands

Section numbers below refer to the full development manual above.

| Command | Purpose | Manual Section |
|---|---|---|
| `00 D1 00 00 00` | Read board TLV information | 7.4 |
| `F0 D8 00 00 05 00 00 00 00 0E` | Read a user-area prefix and check access, not panel configuration | 7.14 |
| `F0 DB 02 00 00` | Clear the stored driver; subsequent DB packets write a complete recipe | 7.10 |
| DA | Configure panel type and scan settings | 7.9 |
| D2 / D3 | Raw / compressed image upload | 7.5 / 7.6 |
| `F0 D4 85 80 00` | Execute the stored driver asynchronously | 7.7 |
| `F0 DE 00 00 01` | `01` busy, `00` idle | 7.12 |

User data, panel drivers and image slots are separate areas. Successful authentication or upload does not prove that high voltage started or the image refreshed.

## Reader Compatibility

Auto mode limits ISO-DEP INF to 240 bytes for normal PN532/PCR532 UART framing. The FMSC image subpacket stride remains 250 bytes; ISO-DEP handles fragmentation.

`--reader pn532` may use up to 253-byte INF when negotiated; `--reader pcr532` uses the same safe policy as auto. Compatibility does not identify a marketed reader model or alter RF power.

No response stops the exchange and run without write, D4, PIN or activation replay. Refresh waiting does not intentionally disable RF. Completion or cancellation attempts RF-off; if the reader is unresponsive, remove the tag.

## Storage and Panel Limits

D2 supports up to 256 packets per slot with a fixed 250-byte stride. D3 independently compresses 2000-byte decoded blocks and retains a 250-byte compressed subpacket stride. APDU counts depend on compression and do not establish decoded capacity.

A0/A1 segmentation retains physical 768×552 geometry and changes RAM/refresh source windows. Each pass configures, uploads and refreshes. It is not a contiguous byte-stream split. The final slot and driver describe only the last half; retention of the other half requires visual testing.

Other controllers have no generic split. A refresh window does not prove electrodes outside it are inactive.

Round E6 panels, high-resolution panels and some recipes still have power or adaptation limits. Software checks geometry, layout, polarity, fingerprints and prohibited commands, but cannot replace wiring and specification verification for unknown glass.
