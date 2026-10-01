/*
BSD LICENSE

Copyright (c) 2019-2026 Superluminal. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

  * Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
  * Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in
    the documentation and/or other materials provided with the
    distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
package raw

MAJOR_VERSION :: 3
MINOR_VERSION :: 0
VERSION :: i32((MAJOR_VERSION << 16) | MINOR_VERSION)
DEFAULT_COLOR :: u32(0xFFFFFFFF)

Suppress_Tail_Call_Optimization :: struct #align (8) {
	suppress_tail_call: [3]i64,
}

SetCurrentThreadName_Proc :: #type proc "c" (name: cstring)
SetCurrentThreadNameN_Proc :: #type proc "c" (name: [^]u8, name_length: u16)
BeginEvent_Proc :: #type proc "c" (id, data: cstring, color: u32)
BeginEventN_Proc :: #type proc "c" (
	id: [^]u8,
	id_length: u16,
	data: [^]u8,
	data_length: u16,
	color: u32,
)
BeginEventWide_Proc :: #type proc "c" (id, data: [^]u16, color: u32)
BeginEventWideN_Proc :: #type proc "c" (
	id: [^]u16,
	id_length: u16,
	data: [^]u16,
	data_length: u16,
	color: u32,
)
EndEvent_Proc :: #type proc "c" () -> Suppress_Tail_Call_Optimization
RegisterFiber_Proc :: #type proc "c" (fiber_id: u64)
UnregisterFiber_Proc :: #type proc "c" (fiber_id: u64)
BeginFiberSwitch_Proc :: #type proc "c" (current_fiber_id, new_fiber_id: u64)
EndFiberSwitch_Proc :: #type proc "c" (fiber_id: u64)

Functions :: struct {
	SetCurrentThreadName:  SetCurrentThreadName_Proc,
	SetCurrentThreadNameN: SetCurrentThreadNameN_Proc,
	BeginEvent:            BeginEvent_Proc,
	BeginEventN:           BeginEventN_Proc,
	BeginEventWide:        BeginEventWide_Proc,
	BeginEventWideN:       BeginEventWideN_Proc,
	EndEvent:              EndEvent_Proc,
	RegisterFiber:         RegisterFiber_Proc,
	UnregisterFiber:       UnregisterFiber_Proc,
	BeginFiberSwitch:      BeginFiberSwitch_Proc,
	EndFiberSwitch:        EndFiberSwitch_Proc,
}

Get_API_Proc :: #type proc "c" (version: i32, functions: ^Functions) -> i32

#assert(size_of(Suppress_Tail_Call_Optimization) == 24)
#assert(align_of(Suppress_Tail_Call_Optimization) == 8)
#assert(size_of(Functions) == 11 * size_of(rawptr))
#assert(align_of(Functions) == align_of(rawptr))
#assert(offset_of(Functions, SetCurrentThreadName) == 0 * size_of(rawptr))
#assert(offset_of(Functions, SetCurrentThreadNameN) == 1 * size_of(rawptr))
#assert(offset_of(Functions, BeginEvent) == 2 * size_of(rawptr))
#assert(offset_of(Functions, BeginEventN) == 3 * size_of(rawptr))
#assert(offset_of(Functions, BeginEventWide) == 4 * size_of(rawptr))
#assert(offset_of(Functions, BeginEventWideN) == 5 * size_of(rawptr))
#assert(offset_of(Functions, EndEvent) == 6 * size_of(rawptr))
#assert(offset_of(Functions, RegisterFiber) == 7 * size_of(rawptr))
#assert(offset_of(Functions, UnregisterFiber) == 8 * size_of(rawptr))
#assert(offset_of(Functions, BeginFiberSwitch) == 9 * size_of(rawptr))
#assert(offset_of(Functions, EndFiberSwitch) == 10 * size_of(rawptr))
