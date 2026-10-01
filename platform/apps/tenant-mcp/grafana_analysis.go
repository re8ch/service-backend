package main

import (
	"errors"
	"fmt"
	"math"
	"sort"
)

func numericArray(value any) ([]float64, error) {
	values, ok := value.([]any)
	if !ok {
		if already, yes := value.([]float64); yes {
			return already, nil
		}
		return nil, errors.New("numeric series is required")
	}
	result := make([]float64, len(values))
	for i, raw := range values {
		number, ok := raw.(float64)
		if !ok || math.IsNaN(number) || math.IsInf(number, 0) {
			return nil, errors.New("numeric series is required")
		}
		result[i] = number
	}
	return result, nil
}

func framesToSeries(value any) map[string][]float64 {
	output := map[string][]float64{}
	for ref, rawResult := range object(object(value)["results"]) {
		for frameIndex, rawFrame := range slice(object(rawResult)["frames"]) {
			frame := object(rawFrame)
			fields := slice(object(frame["schema"])["fields"])
			columns := slice(object(frame["data"])["values"])
			for fieldIndex, rawColumn := range columns {
				field := map[string]any{}
				if fieldIndex < len(fields) {
					field = object(fields[fieldIndex])
				}
				typeName := asString(field, "type")
				if typeName != "number" && typeName != "integer" && typeName != "float" {
					continue
				}
				values := []float64{}
				for _, raw := range slice(rawColumn) {
					if number, ok := raw.(float64); ok && !math.IsInf(number, 0) && !math.IsNaN(number) {
						values = append(values, number)
					}
				}
				if len(values) != 0 {
					name := asString(field, "name")
					if name == "" {
						name = fmt.Sprint(fieldIndex)
					}
					output[fmt.Sprintf("%s.%s.%d", ref, name, frameIndex)] = values
				}
			}
		}
	}
	return output
}

func mean(values []float64) float64 {
	sum := 0.0
	for _, value := range values {
		sum += value
	}
	if len(values) == 0 {
		return 0
	}
	return sum / float64(len(values))
}
func pstdev(values []float64) float64 {
	average := mean(values)
	squared := 0.0
	for _, value := range values {
		squared += (value - average) * (value - average)
	}
	if len(values) == 0 {
		return 0
	}
	return math.Sqrt(squared / float64(len(values)))
}

func indicators(values []float64, requested []any) (map[string]any, error) {
	if len(values) == 0 {
		return nil, errors.New("indicator input is empty")
	}
	output := map[string]any{}
	for _, raw := range requested {
		item := object(raw)
		kind := asString(item, "type")
		name := asString(item, "outputName")
		if name == "" {
			name = kind
		}
		window := intValue(item["window"])
		if window < 1 {
			window = 5
		}
		switch kind {
		case "movingAverage":
			result := []float64{}
			for i := range values {
				start := max(0, i-window+1)
				result = append(result, mean(values[start:i+1]))
			}
			output[name] = result
		case "ema":
			alpha := 2 / float64(window+1)
			if provided, ok := item["alpha"].(float64); ok {
				alpha = provided
			}
			result := []float64{values[0]}
			for _, value := range values[1:] {
				result = append(result, alpha*value+(1-alpha)*result[len(result)-1])
			}
			output[name] = result
		case "changeRate":
			result := []any{nil}
			for i := 1; i < len(values); i++ {
				if values[i-1] == 0 {
					result = append(result, nil)
				} else {
					result = append(result, (values[i]-values[i-1])/math.Abs(values[i-1]))
				}
			}
			output[name] = result
		case "volatility":
			changes := []float64{0}
			for i := 1; i < len(values); i++ {
				if values[i-1] == 0 {
					changes = append(changes, 0)
				} else {
					changes = append(changes, (values[i]-values[i-1])/math.Abs(values[i-1]))
				}
			}
			result := []float64{}
			for i := range changes {
				result = append(result, pstdev(changes[max(0, i-window+1):i+1]))
			}
			output[name] = result
		case "zscore":
			average, deviation := mean(values), pstdev(values)
			result := []float64{}
			for _, value := range values {
				if deviation == 0 {
					result = append(result, 0)
				} else {
					result = append(result, (value-average)/deviation)
				}
			}
			output[name] = result
		case "slope":
			average, mid := mean(values), float64(len(values)-1)/2
			numerator, denominator := 0.0, 0.0
			for i, value := range values {
				x := float64(i) - mid
				numerator += x * (value - average)
				denominator += x * x
			}
			slope := 0.0
			if denominator != 0 {
				slope = numerator / denominator
			}
			var change any
			if values[0] != 0 {
				change = (values[len(values)-1] - values[0]) / math.Abs(values[0])
			}
			trend := "flat"
			if slope > 0 {
				trend = "up"
			}
			if slope < 0 {
				trend = "down"
			}
			output[name] = map[string]any{"slope": slope, "changeRate": change, "trend": trend}
		case "quantile":
			q := .95
			if provided, ok := item["quantile"].(float64); ok {
				q = provided
			}
			ordered := append([]float64{}, values...)
			sort.Float64s(ordered)
			index := min(len(ordered)-1, max(0, int(math.RoundToEven(q*float64(len(ordered)-1)))))
			output[name] = ordered[index]
		default:
			return nil, errors.New("unsupported indicator")
		}
	}
	return output, nil
}

func analyzeSeries(series map[string][]float64, pipeline []any) (map[string]any, error) {
	output := map[string][]float64{}
	for key, values := range series {
		output[key] = append([]float64{}, values...)
	}
	summaries := map[string]any{}
	for _, raw := range pipeline {
		step := object(raw)
		kind := asString(step, "type")
		inputs := []string{}
		for _, value := range slice(step["inputs"]) {
			if name, ok := value.(string); ok {
				inputs = append(inputs, name)
			}
		}
		if len(inputs) == 0 {
			for name := range output {
				inputs = append(inputs, name)
			}
			sort.Strings(inputs)
		}
		for _, name := range inputs {
			if _, ok := output[name]; !ok {
				return nil, errors.New("unknown series: " + name)
			}
		}
		switch kind {
		case "reduce":
			operation := asString(step, "operation")
			name := asString(step, "outputName")
			if name == "" {
				name = operation
			}
			values := map[string]float64{}
			for _, input := range inputs {
				series := output[input]
				if len(series) == 0 {
					return nil, errors.New("cannot reduce an empty series")
				}
				switch operation {
				case "avg":
					values[input] = mean(series)
				case "sum":
					for _, value := range series {
						values[input] += value
					}
				case "min":
					values[input] = series[0]
					for _, value := range series {
						values[input] = math.Min(values[input], value)
					}
				case "max":
					values[input] = series[0]
					for _, value := range series {
						values[input] = math.Max(values[input], value)
					}
				case "count":
					values[input] = float64(len(series))
				default:
					return nil, errors.New("unsupported reduce operation")
				}
			}
			summaries[name] = values
		case "merge", "math":
			name := asString(step, "outputName")
			if name == "" {
				name = "merged"
			}
			if len(inputs) == 0 {
				return nil, errors.New("merge requires series")
			}
			length := len(output[inputs[0]])
			for _, input := range inputs {
				length = min(length, len(output[input]))
			}
			merged := []float64{}
			for i := 0; i < length; i++ {
				sum := 0.0
				for _, input := range inputs {
					sum += output[input][i]
				}
				if asString(step, "operation") == "avg" {
					sum /= float64(len(inputs))
				}
				merged = append(merged, sum)
			}
			output[name] = merged
		case "normalize":
			for _, input := range inputs {
				values := output[input]
				if len(values) == 0 {
					return nil, errors.New("cannot normalize empty series")
				}
				average, deviation := mean(values), pstdev(values)
				normalized := []float64{}
				for _, value := range values {
					if deviation == 0 {
						normalized = append(normalized, 0)
					} else {
						normalized = append(normalized, (value-average)/deviation)
					}
				}
				output[input] = normalized
			}
		case "filter":
			minimum, minOK := step["min"].(float64)
			maximum, maxOK := step["max"].(float64)
			for _, input := range inputs {
				kept := []float64{}
				for _, value := range output[input] {
					if (!minOK || value >= minimum) && (!maxOK || value <= maximum) {
						kept = append(kept, value)
					}
				}
				output[input] = kept
			}
		default:
			return nil, errors.New("unsupported analysis pipeline step")
		}
	}
	return map[string]any{"series": output, "summaries": summaries}, nil
}

func ranks(values []float64) []float64 {
	indices := make([]int, len(values))
	for i := range values {
		indices[i] = i
	}
	sort.Slice(indices, func(i, j int) bool { return values[indices[i]] < values[indices[j]] })
	result := make([]float64, len(values))
	for position := 0; position < len(indices); {
		end := position + 1
		for end < len(indices) && values[indices[end]] == values[indices[position]] {
			end++
		}
		rank := float64(position+end-1)/2 + 1
		for i := position; i < end; i++ {
			result[indices[i]] = rank
		}
		position = end
	}
	return result
}

func correlation(left, right []float64, method string) any {
	if len(left) != len(right) || len(left) < 2 {
		return nil
	}
	if method == "spearman" {
		left, right = ranks(left), ranks(right)
	}
	meanLeft, meanRight := mean(left), mean(right)
	numerator, leftSquares, rightSquares := 0.0, 0.0, 0.0
	for i := range left {
		a, b := left[i]-meanLeft, right[i]-meanRight
		numerator += a * b
		leftSquares += a * a
		rightSquares += b * b
	}
	denominator := math.Sqrt(leftSquares * rightSquares)
	if denominator == 0 {
		return nil
	}
	return numerator / denominator
}

func lagCorrelations(left, right []float64, method string, maxLag int) []map[string]any {
	rows := []map[string]any{}
	for lag := -maxLag; lag <= maxLag; lag++ {
		a, b := left, right
		if lag < 0 {
			offset := min(-lag, len(left))
			a = left[offset:]
			b = right[:max(0, len(right)+lag)]
		} else if lag > 0 {
			a = left[:max(0, len(left)-lag)]
			b = right[min(lag, len(right)):]
		}
		rows = append(rows, map[string]any{"lag": lag, "coefficient": correlation(a, b, method), "sampleCount": len(a), "missingRate": 0.0})
	}
	return rows
}
