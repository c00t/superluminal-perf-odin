package superluminal_perf

import "raw"

@(private)
empty_utf8: u8
@(private)
empty_utf16: u16

@(no_instrumentation)
make_color :: #force_inline proc(r, g, b: u8) -> u32 {
	return (u32(r) << 24) | (u32(g) << 16) | (u32(b) << 8) | 0xFF
}

@(private, no_instrumentation)
require_initialized :: #force_inline proc() {
	when ENABLED && LINK_MODE == "dynamic" {
		if module.handle == nil {
			panic("Superluminal is not initialized")
		}
	}
}

@(private, no_instrumentation)
checked_length :: #force_inline proc(length: int) -> u16 {
	if length > 65535 {
		panic("Superluminal string length exceeds 65535")
	}
	return u16(length)
}

@(no_instrumentation)
set_current_thread_name :: #force_inline proc(name: string) {
	when ENABLED {
		length := checked_length(len(name))
		require_initialized()
		pointer := raw_data(name)
		if length == 0 {
			pointer = ([^]u8)(&empty_utf8)
		}
		when LINK_MODE == "static" {
			raw.SetCurrentThreadName_N(pointer, length)
		} else {
			module.functions.SetCurrentThreadNameN(pointer, length)
		}
	}
}

// UTF-8 lengths are bytes. Keep id and color stable for the event's lifetime.
@(no_instrumentation)
begin_event :: #force_inline proc(id: string, data: string = "", color: u32 = DEFAULT_COLOR) {
	when ENABLED {
		id_length := checked_length(len(id))
		data_length := checked_length(len(data))
		require_initialized()
		id_pointer := raw_data(id)
		if id_length == 0 {
			id_pointer = ([^]u8)(&empty_utf8)
		}
		data_pointer: [^]u8
		if data_length != 0 {
			data_pointer = raw_data(data)
		}
		when LINK_MODE == "static" {
			raw.BeginEvent_N(id_pointer, id_length, data_pointer, data_length, color)
		} else {
			module.functions.BeginEventN(id_pointer, id_length, data_pointer, data_length, color)
		}
	}
}

// UTF-16 lengths are code units, including both units of each surrogate pair.
@(no_instrumentation)
begin_event_wide :: #force_inline proc(id: []u16, data: []u16 = nil, color: u32 = DEFAULT_COLOR) {
	when ENABLED {
		id_length := checked_length(len(id))
		data_length := checked_length(len(data))
		require_initialized()
		id_pointer := raw_data(id)
		if id_length == 0 {
			id_pointer = ([^]u16)(&empty_utf16)
		}
		data_pointer: [^]u16
		if data_length != 0 {
			data_pointer = raw_data(data)
		}
		when LINK_MODE == "static" {
			raw.BeginEvent_Wide_N(id_pointer, id_length, data_pointer, data_length, color)
		} else {
			module.functions.BeginEventWideN(
				id_pointer,
				id_length,
				data_pointer,
				data_length,
				color,
			)
		}
	}
}

@(no_instrumentation)
end_event :: #force_inline proc() {
	when ENABLED {
		require_initialized()
		// Preserve the C struct-return ABI even though its contents are unused.
		when LINK_MODE == "static" {
			_ = raw.EndEvent()
		} else {
			_ = module.functions.EndEvent()
		}
	}
}

// The deferred end runs at the end of the caller's lexical scope.
@(no_instrumentation, deferred_none = end_event)
scope :: #force_inline proc(id: string, data: string = "", color: u32 = DEFAULT_COLOR) {
	begin_event(id, data, color)
}

@(no_instrumentation)
register_fiber :: #force_inline proc(fiber_id: u64) {
	when ENABLED {
		require_initialized()
		when LINK_MODE == "static" {
			raw.RegisterFiber(fiber_id)
		} else {
			module.functions.RegisterFiber(fiber_id)
		}
	}
}

@(no_instrumentation)
unregister_fiber :: #force_inline proc(fiber_id: u64) {
	when ENABLED {
		require_initialized()
		when LINK_MODE == "static" {
			raw.UnregisterFiber(fiber_id)
		} else {
			module.functions.UnregisterFiber(fiber_id)
		}
	}
}

@(no_instrumentation)
begin_fiber_switch :: #force_inline proc(current_fiber_id, new_fiber_id: u64) {
	when ENABLED {
		require_initialized()
		when LINK_MODE == "static" {
			raw.BeginFiberSwitch(current_fiber_id, new_fiber_id)
		} else {
			module.functions.BeginFiberSwitch(current_fiber_id, new_fiber_id)
		}
	}
}

@(no_instrumentation)
end_fiber_switch :: #force_inline proc(fiber_id: u64) {
	when ENABLED {
		require_initialized()
		when LINK_MODE == "static" {
			raw.EndFiberSwitch(fiber_id)
		} else {
			module.functions.EndFiberSwitch(fiber_id)
		}
	}
}
