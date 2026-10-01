package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type boundedBuffer struct {
	buffer    bytes.Buffer
	limit     int
	truncated bool
}

func (b *boundedBuffer) Write(value []byte) (int, error) {
	length := len(value)
	remaining := b.limit - b.buffer.Len()
	if remaining > 0 {
		if remaining > length {
			remaining = length
		}
		_, _ = b.buffer.Write(value[:remaining])
	}
	if length > remaining {
		b.truncated = true
	}
	return length, nil
}

func commandResult(parent context.Context, argv []string, input *string, seconds int) map[string]any {
	operation := "op_" + newRef()
	started := time.Now().Unix()
	maximum := 3600
	if configured, err := strconv.Atoi(os.Getenv("MAX_COMMAND_TIMEOUT_SECONDS")); err == nil && configured > 0 {
		maximum = configured
	}
	seconds = max(1, min(seconds, maximum))
	ctx, cancel := context.WithTimeout(parent, time.Duration(seconds)*time.Second)
	defer cancel()
	maximumOutput := 4 << 20
	if configured, err := strconv.Atoi(os.Getenv("MAX_OUTPUT_BYTES")); err == nil && configured > 0 {
		maximumOutput = configured
	}
	stdout, stderr := &boundedBuffer{limit: maximumOutput}, &boundedBuffer{limit: maximumOutput}
	command := exec.CommandContext(ctx, argv[0], argv[1:]...)
	command.Stdout, command.Stderr = stdout, stderr
	if input != nil {
		command.Stdin = strings.NewReader(*input)
	}
	err := command.Run()
	exitCode := 0
	if err != nil {
		var exited *exec.ExitError
		if errors.As(err, &exited) {
			exitCode = exited.ExitCode()
		} else {
			exitCode = 1
			stderr.Write([]byte(err.Error()))
		}
	}
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		exitCode = 124
		stderr = &boundedBuffer{limit: maximumOutput}
		stderr.Write([]byte("command timed out"))
	}
	return map[string]any{"operation_id": operation, "argv": argv, "exit_code": exitCode, "stdout": stdout.buffer.String(), "stderr": stderr.buffer.String(), "truncated": stdout.truncated || stderr.truncated, "started_at": started, "finished_at": time.Now().Unix()}
}
