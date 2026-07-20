# Cyrinx 3.0 API / ABI Inventory

This document inventories every public symbol in the Swift library (`Sources/Cyrinx`) and the C headers (`Sources/CCyrinx/include`) and classifies it for the Cyrinx 3.0 release.

## Classification Definitions
- **Retain**: Keep the symbol as-is or with minor documentation updates in the stable Cyrinx 3.0 API.
- **Deprecate**: Keep as a deprecated compatibility shim in 3.0, to be removed in a future major release.
- **Move to Experimental**: Move the symbol to the `CyrinxExperimental` or `CyrinxSimulation` modules, removing it from the production surface.
- **Replace**: Remove or replace with a new 3.0 architectural equivalent.

---

## 1. Swift Public Symbol Inventory

### Classes & Actors
| Symbol | Source File | Classification | Explanation / Target 3.0 Equivalent |
| :--- | :--- | :--- | :--- |
| `AcousticCalibration` | `AcousticCalibration.swift` | **Retain** | Kept for hardware-specific gain and volume optimization; will eventually move to `CyrinxAppleAudio`. |
| `AcousticNoiseScanner` | `AcousticNoiseScanner.swift` | **Retain** | Essential for active notch-filter identification. |
| `CyrinxSession` | `Cyrinx.swift` | **Deprecate** | Replaced by `CyrinxTransport` and `CyrinxConnection`. |
| `RawAcousticMacLink` | `RawAcousticLink.swift` | **Deprecate** | Replaced by the 3.0 loopback simulation mechanisms. |

### Structs
| Symbol | Source File | Classification | Explanation / Target 3.0 Equivalent |
| :--- | :--- | :--- | :--- |
| `AcousticPHYDecodeResult` | `AcousticPHYDebug.swift` | **Move to Experimental** | Belongs in `CyrinxExperimental` for DSP testing. |
| `MCSRecommendation` | `AdaptiveSounder.swift` | **Deprecate** | Replaced by `LinkEstimate` snapshots. |
| `AudioBackendDiagnostics` | `AudioScaffold.swift` | **Retain** | Necessary for tracking low-level hardware callback performance. |
| `BulkPHY` | `BulkPHY.swift` | **Deprecate** | Replaced by `CyrinxConnection` message transmission. |
| `BulkPHY.Configuration` | `BulkPHY.swift` | **Deprecate** | Replaced by stable on-wire profile IDs in the registry. |
| `BulkPHY.Geometry` | `BulkPHY.swift` | **Deprecate** | Made private to C core. |
| `BulkPHY.AutomaticDiversityDiagnostics` | `BulkPHY.swift` | **Replace** | Replaced by `LinkEstimate` and diagnostic bundles. |
| `BulkPHY.Decoded` | `BulkPHY.swift` | **Replace** | Replaced by `CyrinxTransfer` token updates. |
| `StreamFlags` | `Cyrinx.swift` | **Deprecate** | Replaced by `SendOptions` configurations. |
| `ReceivedMessage` | `Cyrinx.swift` | **Replace** | Replaced by `CyrinxTransfer` state. |
| `Config` | `Cyrinx.swift` | **Deprecate** | Replaced by `SendOptions` and profile definitions. |
| `ARCPolicy` | `Cyrinx.swift` | **Deprecate** | Replaced by the core policy registry. |
| `Metrics` | `Cyrinx.swift` | **Replace** | Replaced by `LinkEstimate`. |
| `PHYComplex` | `PHY.swift` | **Move to Experimental** | DSP math helper; internal to core math routines. |
| `PHYStubConfig` | `PHY.swift` | **Move to Experimental** | Replaced by simulation configurations in `CyrinxSimulation`. |
| `PHYStubSequentialState` | `PHY.swift` | **Move to Experimental** | Moved to simulation/test suite package. |
| `RawAcousticDiagnostics` | `RawAcousticLink.swift` | **Deprecate** | Diagnostic stats consolidated into `LinkEstimate`. |
| `ChannelMetrics` | `RepositioningGuidance.swift` | **Replace** | Replaced by `LinkEstimate` diagnostics. |
| `RepositioningAdvice` | `RepositioningGuidance.swift` | **Replace** | Replaced by dynamic state advice in connection snapshots. |
| `SimulationOptions` | `Simulation.swift` | **Move to Experimental** | Exposed under `CyrinxSimulation`. |
| `SimulationResult` | `Simulation.swift` | **Move to Experimental** | Exposed under `CyrinxSimulation`. |
| `VDSPOFDMConfig` | `VDSPPHY.swift` | **Replace** | Configuration details absorbed by core C profile definitions. |
| `VDSPDCSSConfig` | `VDSPPHY.swift` | **Replace** | Configuration details absorbed by core C profile definitions. |

### Enums
| Symbol | Source File | Classification | Explanation / Target 3.0 Equivalent |
| :--- | :--- | :--- | :--- |
| `AcousticPHYDebug` | `AcousticPHYDebug.swift` | **Move to Experimental** | Debug helpers; belongs in `CyrinxExperimental`. |
| `MCSTier` | `AdaptiveSounder.swift` | **Deprecate** | Replaced by profiles in `CCyrinxCore`. |
| `TransportBackend` | `AudioScaffold.swift` | **Retain** | Retained for Apple hardware selection. |
| `AudioBackendState` | `AudioScaffold.swift` | **Retain** | State tracking of active audio hardware. |
| `AudioBackendError` | `AudioScaffold.swift` | **Retain** | Wrapped or reported inside platform adapters. |
| `RawAcousticDebug` | `BasicToneCodec.swift` | **Move to Experimental** | Belongs in `CyrinxExperimental`. |
| `BulkPHY.DiversityReceiver` | `BulkPHY.swift` | **Deprecate** | Replaced by `LinkEstimate` mic selections. |
| `BulkPHY.AutomaticDiversityReason` | `BulkPHY.swift` | **Replace** | Handled internally in C core diversity selection. |
| `Role` | `Cyrinx.swift` | **Deprecate** | Handled automatically during peer handshake. |
| `QoS` | `Cyrinx.swift` | **Replace** | Replaced by `SendOptions`. |
| `StreamPriority` | `Cyrinx.swift` | **Retain** | Retained for outbound message scheduling. |
| `Gear` | `Cyrinx.swift` | **Deprecate** | Replaced by profiles. |
| `Event` | `Cyrinx.swift` | **Replace** | Replaced by `ConnectionState` transitions. |
| `Cyrinx` | `Cyrinx.swift` | **Retain** | Retained as umbrella namespace for metadata. |
| `PHYStubMode` | `PHY.swift` | **Move to Experimental** | Relocated to `CyrinxSimulation`. |
| `PHYStub` | `PHY.swift` | **Move to Experimental** | Relocated to `CyrinxSimulation`. |
| `RawAcousticCodec` | `RawAcousticCodec.swift` | **Move to Experimental** | Legacy non-OFDM codecs moved to experimental. |
| `RepositioningHint` | `RepositioningGuidance.swift` | **Replace** | Combined into dynamic link diagnostics. |
| `SimulationProfile` | `Simulation.swift` | **Move to Experimental** | Moved to `CyrinxSimulation`. |
| `SimulationRunner` | `Simulation.swift` | **Move to Experimental** | Moved to `CyrinxSimulation`. |
| `VDSPPHYError` | `VDSPPHY.swift` | **Replace** | Errors standardized to `CyrinxError`. |
| `VDSPPHY` | `VDSPPHY.swift` | **Replace** | Replaced by core C PHY modulators. |

### Global Functions
| Symbol | Source File | Classification | Explanation / Target 3.0 Equivalent |
| :--- | :--- | :--- | :--- |
| `recommendMCS(...)` | `AdaptiveSounder.swift` | **Replace** | Replaced by core C policy reducer. |
| `bitLoading(...)` | `AdaptiveSounder.swift` | **Replace** | Handled internally in `CCyrinxDSP`. |
| `repositioningAdvice(...)` | `RepositioningGuidance.swift` | **Replace** | Dynamic advice synthesized from C `LinkEstimate`. |

---

## 2. C Core Public Symbol Inventory

### Types & Enums
| Symbol | Header File | Classification | Explanation / Target 3.0 Equivalent |
| :--- | :--- | :--- | :--- |
| `cyrinx_session_t` | `cyrinx.h` | **Retain** | Main session opaque struct handle. |
| `cyrinx_status_t` | `cyrinx.h` | **Retain** | Status return codes. |
| `cyrinx_role_t` | `cyrinx.h` | **Retain** | Engine negotiation roles. |
| `cyrinx_qos_t` | `cyrinx.h` | **Deprecate** | Replaced by frame-level configuration tags. |
| `cyrinx_security_mode_t` | `cyrinx.h` | **Retain** | Reserved for cryptographic transport. |
| `cyrinx_frame_type_t` | `cyrinx.h` | **Retain** | Underlying frame categorization. |
| `cyrinx_gear_t` | `cyrinx.h` | **Deprecate** | Replaced by profile IDs in the registry. |
| `cyrinx_event_t` | `cyrinx.h` | **Replace** | Replaced by unified state reducer snapshots. |
| `cyrinx_detrng` | `cyrinx_bulk.h` | **Move to Experimental** | Relocated to internal `CCyrinxDSP` namespace. |

### C API Functions
| Symbol | Header File | Classification | Explanation / Target 3.0 Equivalent |
| :--- | :--- | :--- | :--- |
| `cyrinx_version` | `cyrinx.h` | **Retain** | Standard version string retrieval. |
| `cyrinx_status_name` | `cyrinx.h` | **Retain** | Translates numeric status to symbol name. |
| `cyrinx_status_description`| `cyrinx.h` | **Retain** | Translates status to readable explanation. |
| `cyrinx_default_config` | `cyrinx.h` | **Replace** | Configuration values replaced by registered profiles. |
| `cyrinx_default_arc_policy` | `cyrinx.h` | **Replace** | Policies defined in core registry. |
| `cyrinx_open` | `cyrinx.h` | **Retain** | Instantiates native transport context. |
| `cyrinx_close` | `cyrinx.h` | **Retain** | Clean up transport context. |
| `cyrinx_start` | `cyrinx.h` | **Retain** | Starts active hardware/session tracking. |
| `cyrinx_send_stream` | `cyrinx.h` | **Replace** | Replaced by block-oriented packet write API. |
| `cyrinx_recv_stream` | `cyrinx.h` | **Replace** | Replaced by block-oriented packet read API. |
| `cyrinx_get_metrics` | `cyrinx.h` | **Replace** | Replaced by unified session state query. |
| `cyrinx_set_arc_policy` | `cyrinx.h` | **Replace** | Adaptation policies handled via profiles. |
| `cyrinx_ingest_frame` | `cyrinx.h` | **Retain** | Feeds external frames into the decoder. |
| `cyrinx_update_channel_report`| `cyrinx.h` | **Replace** | Incorporated directly into state-machine reductions. |
| `cyrinx_link_in_memory` | `cyrinx.h` | **Move to Experimental** | Replaced by test loopback mocks. |
| `cyrinx_arc_select_gear` | `cyrinx.h` | **Replace** | Handled internally in core state engine. |
| `cyrinx_detrng_*` | `cyrinx_bulk.h` | **Move to Experimental** | DSP math helper functions; made internal. |
| `cyrinx_prbs_bits` | `cyrinx_bulk.h` | **Move to Experimental** | DSP math helper functions; made internal. |
| `cyrinx_bulk_crc32` | `cyrinx_bulk.h` | **Move to Experimental** | Core math utilities made internal. |
| `cyrinx_zc_generate` | `cyrinx_phy.h` | **Move to Experimental** | Part of internal CCyrinxDSP module. |
| `cyrinx_estimate_cfo_hz` | `cyrinx_phy.h` | **Move to Experimental** | Part of internal CCyrinxDSP module. |
| `cyrinx_select_cp_samples` | `cyrinx_phy.h` | **Move to Experimental** | Part of internal CCyrinxDSP module. |
| `cyrinx_phy_stub_*` | `cyrinx_phy.h` | **Move to Experimental** | Stubs moved to `CyrinxSimulation`. |
