package contracts

import perf "../.."
import raw "../../raw"
import "core:testing"

all_present :: proc(f: raw.Functions) -> bool {
	return(
		f.SetCurrentThreadName != nil &&
		f.SetCurrentThreadNameN != nil &&
		f.BeginEvent != nil &&
		f.BeginEventN != nil &&
		f.BeginEventWide != nil &&
		f.BeginEventWideN != nil &&
		f.EndEvent != nil &&
		f.RegisterFiber != nil &&
		f.UnregisterFiber != nil &&
		f.BeginFiberSwitch != nil &&
		f.EndFiberSwitch != nil \
	)
}

all_absent :: proc(f: raw.Functions) -> bool {
	return(
		f.SetCurrentThreadName == nil &&
		f.SetCurrentThreadNameN == nil &&
		f.BeginEvent == nil &&
		f.BeginEventN == nil &&
		f.BeginEventWide == nil &&
		f.BeginEventWideN == nil &&
		f.EndEvent == nil &&
		f.RegisterFiber == nil &&
		f.UnregisterFiber == nil &&
		f.BeginFiberSwitch == nil &&
		f.EndFiberSwitch == nil \
	)
}


// One lifecycle test keeps the process-global native module externally serialized.
@(test)
contracts :: proc(t: ^testing.T) {
	testing.expect_value(t, perf.make_color(0x12, 0x34, 0x56), u32(0x123456ff))
	testing.expect_value(t, perf.make_color(0, 0, 0), u32(0x000000ff))
	testing.expect_value(t, perf.make_color(255, 255, 255), u32(0xffffffff))
	testing.expect(t, !raw.free(nil))
	empty: raw.Module
	testing.expect(t, raw.free(&empty))
	when !perf.ENABLED {
		testing.expect_value(t, perf.init(), perf.Error.Disabled)
		testing.expect(t, !perf.is_initialized())
		module, err := raw.load_from(nil)
		testing.expect_value(t, err, raw.Error.Disabled)
		testing.expect(t, module.handle == nil && all_absent(module.functions))
		module, err = raw.load()
		testing.expect_value(t, err, raw.Error.Disabled)
		testing.expect(t, module.handle == nil && all_absent(module.functions))
		// Disabled calls neither validate lengths nor require initialization.
		bytes: [65536]u8
		units: [65536]u16
		perf.set_current_thread_name(string(bytes[:]))
		perf.begin_event(string(bytes[:]), string(bytes[:]))
		perf.end_event()
		perf.begin_event_wide(units[:], units[:])
		perf.end_event()
		{perf.scope("Disabled scope")}
		perf.register_fiber(1)
		perf.begin_fiber_switch(1, 2)
		perf.end_fiber_switch(2)
		perf.unregister_fiber(1)
		testing.expect(t, perf.shutdown())
	} else {
		contracts_enabled(t)
	}
}
