# SPDX-FileCopyrightText: Copyright (c) 2023-present NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
# Owner(s): ["module: nvfuser"]

import itertools
from functools import partial, wraps

import torch
from torch.testing import make_tensor

from pytest_core import OpInfo, SampleInput, ErrorSample
from pytest_utils import make_number, find_nonmatching_dtype, is_floating_dtype
from nvfuser import DataType


def broadcast_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # jax.lax.broadcast(operand, sizes)
    # add new dimensions to left-hand-side of tensor
    # dims = tuple(range(len(sizes), len(sizes) + np.ndim(operand)))
    # return broadcast_in_dim(operand, tuple(sizes) + np.shape(operand), dims)

    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    fewer_original_axes = (
        ([2, 3], [True, False]),
        RuntimeError,
        "Invalid broadcast, number of false entries in is_broadcast_dim expected to be",
    )

    greater_original_axes = (
        ([2, 3], [True, False, False, False]),
        RuntimeError,
        "Invalid broadcast, number of false entries in is_broadcast_dim expected to be",
    )

    error_cases = [
        fewer_original_axes,
        greater_original_axes,
    ]
    for es in error_cases:
        ex_case, ex_type, ex_str = es
        input_shape, bcast_dims = ex_case
        input_tensor = make_arg(input_shape)
        yield SampleInput(input_tensor, bcast_dims), ex_type, ex_str


def broadcast_in_dim_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    # The first 5 test cases below are taken from JAX's broadcast_in_dim tests
    #   https://github.com/google/jax/blob/main/tests/lax_test.py#L1171
    # input shape, output shape, bcast_dims
    cases = (
        ([2], [2, 2], [0]),
        ([2], [2, 2], [1]),
        ([2], [2, 3], [0]),
        ([], [2, 3], []),
        ([1], [2, 3], [1]),
        ((4, 6, 3, 1), (5, 4, 7, 6, 3, 6, 6), (1, 3, 4, 5)),
    )

    for input_shape, output_shape, bcast_dims in cases:
        a = make_arg(input_shape)
        yield SampleInput(a, output_shape, bcast_dims)


def broadcast_in_dim_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # jax.lax.broadcast_in_dim(operand, shape, broadcast_dimensions)
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    # 1. Every dimension in the input tensor must be used in broadcast_dimensions.
    missing_axis_in_bcast_dims = (
        ([2, 2], [2, 2, 3], [0]),
        RuntimeError,
        "The broadcast dimensions should match the input dimensions.",
    )

    # 2. New shape has weakly more dimentions than the original tensor.
    fewer_dims_in_output_shape = (
        ([2, 2], [2], [0]),
        RuntimeError,
        "The new shape is expected to be greater-then-or-equal to the input",
    )

    # 3. broadcast_dimensions is an ascending sequence of integers.
    descending_broadcast_dimensions = (
        ([2, 2], [2, 2], [1, 0]),
        RuntimeError,
        "Broadcast dimension is not greater than the previous value.",
    )

    # 4. Each broadcast dimension is within the new shape.
    out_of_bounds_broadcast_dimensions = (
        ([2, 2], [2, 2], [0, 2]),
        RuntimeError,
        "Invalid broadcast_dims value.",
    )

    # 5. The original tensor is not broadcastable to desired shape.
    # tensor.shape[idx] == 1 or tensor.shape[idx] == output_shape[new_idx]
    #
    # Jax Exception:
    # TypeError: broadcast_in_dim operand dimension sizes must either be 1,
    # or be equal to their corresponding dimensions in the target broadcast shape;
    # got operand of shape (2, 3), target broadcast shape (2, 3, 4), broadcast_dimensions (0, 2)
    not_broadcastable = (
        ([2, 3], [2, 3, 4], [0, 2]),
        RuntimeError,
        "Invalid broadcast_dims value.",
    )

    # 6. TypeError: broadcast_in_dim shape must have every element be nonnegative, got (-1, 2, 3).
    negative_shape = (
        ([2, 3], [2, 3, -1], [0, 1]),
        RuntimeError,
        "Invalid broadcast_dims value.",
    )

    # TODO add exceptions for not_broadcastable, negative output shape
    error_cases = [
        missing_axis_in_bcast_dims,
        fewer_dims_in_output_shape,
        descending_broadcast_dimensions,
        out_of_bounds_broadcast_dimensions,
        # not_broadcastable,
        # negative_shape,
    ]
    for es in error_cases:
        ex_case, ex_type, ex_str = es
        input_shape, output_shape, bcast_dims = ex_case
        input_tensor = make_arg(input_shape)
        yield SampleInput(input_tensor, output_shape, bcast_dims), ex_type, ex_str


def cat_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    # concatenating tensors along singleton, broadcast dimensions is unsupported by nvFuser.
    # https://github.com/NVIDIA/Fuser/issues/224
    # shapes, dim
    cases = [
        ([(3,)], 0),  # single tensor provided
        # 1D
        ([(2,), (3,)], 0),
        ([(2,), (4,)], 0),
        ([(0,), (2,)], 0),
        ([(0,), (2,)], -1),
        ([(2, 3), (2, 4)], 1),
        ([(2, 3), (2, 4), (2, 5)], 1),
    ]

    for shapes, dim in cases:
        yield SampleInput([make_arg(s) for s in shapes], dim)


def cat_error_generator(op, dtype=torch.float32, requires_grad: bool = False, **kwargs):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )
    # shapes, dim, exception type, exception string
    empty_input_tensors = (
        ([], 0),
        RuntimeError,
        "Attempting to concatenate empty list of tensors",
    )
    positive_dim = (([(1,), (2,)], 1), RuntimeError, "Invalid dimension to cat")
    negative_dim = (([(2,), (2,)], -2), RuntimeError, "Invalid dimension to cat")
    # All tensors must have same number of dimension"
    ndims_mismatch = (
        ([(2,), (2, 3)], 0),
        RuntimeError,
        "Unexpected number of dimensions",
    )
    # All tensors must have same shape except for the cat dimension
    shape_mismatch = (([(2, 3), (4, 5)], 0), RuntimeError, "known_size == this_size")

    error_cases = [
        empty_input_tensors,
        positive_dim,
        negative_dim,
        ndims_mismatch,
        shape_mismatch,
    ]

    for case, ex_type, ex_str in error_cases:
        shapes, dim = case
        yield SampleInput([make_arg(s) for s in shapes], dim), ex_type, ex_str


def define_tensor_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    yield SampleInput(symbolic_sizes=[-1], contiguity=[True])


def define_tensor_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    """
    "define_tensor",
    [](FusionDefinition& self,
        std::vector<int64_t>& sizes,
        std::vector<int64_t>& strides,
        PrimDataType dtype = DataType::Float,
        bool static_sizes = false,
        bool is_cpu = false) -> Tensor {
    ---
    "define_tensor",
    [](FusionDefinition& self,
        std::vector<int64_t>& symbolic_sizes,
        std::vector<std::optional<bool>>& contiguity,
        PrimDataType dtype = DataType::Float,
        bool is_cpu = false) -> Tensor {
    """

    MINIMUM_SYMBOLIC_SIZE = -1
    INT64_MAX = 9223372036854775807
    MAX_TENSOR_DIMS = 8

    check_size_contiguity_match = ErrorSample(
        {
            "symbolic_sizes": [-1, -1],
            "contiguity": [True, True, True],
            "dtype": DataType.Float,
        },
        "The size of contiguity must equal to the number of non-broadcasting IterDomains",
    )

    check_empty_tensor_size = ErrorSample(
        {"symbolic_sizes": [], "contiguity": []},
        "Empty tensor is unsupported.",
    )

    check_max_tensor_size = ErrorSample(
        {
            "symbolic_sizes": [-1 for _ in range(MAX_TENSOR_DIMS + 1)],
            "contiguity": [True for _ in range(MAX_TENSOR_DIMS + 1)],
        },
        "The specified tensor dimensionality exceeds the max tensor size for nvfuser.",
    )

    check_above_size_range = ErrorSample(
        {"symbolic_sizes": [INT64_MAX + 1], "contiguity": [True]},
        "define_tensor(): incompatible function arguments",
        TypeError,
    )

    check_below_size_range = ErrorSample(
        {"symbolic_sizes": [MINIMUM_SYMBOLIC_SIZE - 1], "contiguity": [True]},
        "The value -2 at index 0 was neither symbolic(-1), zero_element(0), broadcast(1), or static(>1)",
    )

    check_contiguity_unknown_values = ErrorSample(
        {"symbolic_sizes": [10], "contiguity": [-1]},
        "define_tensor(): incompatible function arguments.",
        TypeError,
    )

    check_symbolic_sizes_unknown_dtypes = ErrorSample(
        {"symbolic_sizes": [10.0], "contiguity": [True]},
        "define_tensor(): incompatible function arguments.",
        TypeError,
    )

    # TODO: Fix empty and maximum tensor dimensionality error checks.
    # TODO: Add invalid argument checks for contiguity.
    error_cases = [
        check_size_contiguity_match,
        # check_empty_tensor_size,
        # check_max_tensor_size,
        check_above_size_range,
        check_below_size_range,
        # check_contiguity_unknown_values,
        check_symbolic_sizes_unknown_dtypes,
    ]

    input_tensor = make_tensor(
        (10, 10), device="cuda", dtype=dtype, requires_grad=requires_grad
    )
    for es in error_cases:
        yield SampleInput(input_tensor, **es.kwargs), es.ex_type, es.ex_str


# TODO Add small value, large value, and extremal-valued samples
def elementwise_unary_generator(
    op: OpInfo,
    dtype: torch.dtype,
    requires_grad: bool = False,
    *,
    supports_numbers: bool = True,
    **kwargs,
):
    low = None if op.domain.low is None else max(-9, op.domain.low)
    high = None if op.domain.high is None else min(9, op.domain.high)
    make_arg = partial(
        make_tensor,
        device="cuda",
        dtype=dtype,
        low=low,
        high=high,
        requires_grad=requires_grad,
        **kwargs,
    )

    shapes = (
        # TODO: restore size zero cases
        # (0, 2, 1),
        # (5, 0, 3),
        (),
        (11,),
        (4, 4),
        (1024, 1024),
        (64, 64, 64),
    )

    # Typical inputs
    for shape in shapes:
        yield SampleInput(make_arg(shape))

    # Noncontiguous inputs
    for shape in shapes:
        yield SampleInput(make_arg(shape, noncontiguous=True))


def _elementwise_unary_torch(op):
    @wraps(op)
    def _fn(x):
        if isinstance(x, torch.Tensor):
            return op(x)
        return op(torch.tensor(x)).item()

    return _fn


def full_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # torch.full(size, fill_value, dtype=None)

    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    # Error: Trying to create tensor with negative dimension
    negative_input_shape = [2, -2]
    yield SampleInput(
        negative_input_shape, make_number(dtype), dtype
    ), RuntimeError, "extent_int >= 0"


def gather_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # torch.gather(input: Tensor, dim: int, index: LongTensor)
    # * input and index tensors have same ndims.
    # * index tensors must be smaller than input tensor along all dims except specified axis.

    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )
    make_index = partial(
        make_tensor, device="cuda", dtype=torch.long, requires_grad=False
    )

    # a.shape, dim, b.shape
    cases = (
        ((4, 2, 3), 0, (8, 2, 3)),
        ((4, 2, 3), 1, (4, 1, 3)),
        ((4, 2, 3), 2, (4, 2, 5)),
        ((4,), 0, (8)),
        ((4,), 0, (1)),
        ((4, 1), 0, (3, 1)),
        ((4, 1), 1, (4, 5)),
        # negative dim
        ((4, 2, 3), -3, (8, 2, 3)),
        ((4, 2, 3), -2, (4, 1, 3)),
        ((4, 2, 3), -1, (4, 2, 5)),
        ((4,), -1, (8)),
        ((4,), -1, (1)),
        ((4, 1), -2, (3, 1)),
        ((4, 1), -1, (4, 5)),
        # nvfuser gather does not support broadcast non-axis dimensions
    )

    for shape_a, dim, shape_b in cases:
        a = make_arg(shape_a)
        b = make_index(shape_b, low=0, high=shape_a[dim])
        yield SampleInput(a, b, dim)


def index_select_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )
    make_index = partial(make_tensor, device="cuda", requires_grad=False)

    # a.shape, dim, b.shape
    cases = (
        ((4, 2, 3), 0, (8)),
        ((4, 2, 3), 1, (7)),
        ((4, 2, 3), 2, (2)),
        ((4,), 0, (8)),
        ((4,), 0, (1)),
        ((4, 1), 0, (3)),
        ((4, 1), 1, (5)),
        ((1, 0, 3), 0, (8)),
    )

    for shape_a, dim, shape_b in cases:
        for index_dtype in [torch.int, torch.long]:
            a = make_arg(shape_a)
            b = make_index(shape_b, low=0, high=shape_a[dim], dtype=index_dtype)
            yield SampleInput(a, b, dim)


def index_select_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # torch.index_select(input: Tensor, dim: int, index: LongTensor)
    # * dim is within bounds
    # * index is a 1D vector
    # * index array can't have zero elements
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )
    make_index = partial(make_tensor, device="cuda", requires_grad=False)

    input_shape = (4, 2)
    index_shape = (8,)

    a = make_arg(input_shape)

    # dim, exception type, exception string
    positive_axis = (2, RuntimeError, "index_select on invalid axis")
    negative_axis = (-3, RuntimeError, "index_select on invalid axis")

    error_cases = [
        positive_axis,
        negative_axis,
    ]

    for dim, ex_type, ex_str in error_cases:
        b = make_index(index_shape, low=0, high=10, dtype=torch.long)
        yield SampleInput(a, b, dim), ex_type, ex_str

    # TODO add index dtype check
    # b = make_index(index_shape, low=0, high=input_shape[0], dtype=torch.float)
    # yield SampleInput(a, b, 0), RuntimeError, "index tensor can only be int or long dtype."

    # TODO add index out-of-bounds check
    # b = make_index(index_shape, low=10, high=100, dtype=torch.long)
    # yield SampleInput(a, b, 0), RuntimeError, "out of bounds index value."


def iota_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # torch.arange(start=0, end, step=1, dtype=None)
    # nvfuser.iota(length, start, step, dtype)
    #
    # length, start, step are not complex numbers and are finite numbers.
    # step cannot be 0

    yield SampleInput(
        make_number(torch.complex64, low=1),
        make_number(dtype, low=0),
        make_number(dtype, low=0),
        dtype,
    ), RuntimeError, "length must be integer"

    yield SampleInput(
        make_number(torch.int64, low=1),
        make_number(torch.complex64),
        make_number(dtype, low=0),
        dtype,
    ), RuntimeError, "iota: start dtype does not match specified dtype argument"

    yield SampleInput(
        make_number(torch.int64, low=1),
        make_number(dtype, low=0),
        make_number(torch.complex64),
        dtype,
    ), RuntimeError, "iota: step dtype does not match specified dtype argument"

    if is_floating_dtype(dtype):
        yield SampleInput(
            make_number(torch.int64, low=1),
            float("inf"),
            float("inf"),
            dtype,
        ), RuntimeError, "iota: length, start, step must be finite numbers."

    zero_step = torch.tensor([0], dtype=dtype).item()
    yield SampleInput(
        10, make_number(dtype), zero_step, dtype
    ), RuntimeError, "iota: step value must not equal zero."


def pad_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # Nvfuser - fd.ops.pad(Tensor arg, std::vector<int64_t>& pad_widths, std::optional<Scalar> value)
    # Jax ----- jax.lax.pad(operand, padding_value, padding_config)
    # PyTorch - torch.nn.functional.pad(input, pad, mode='constant', value=None)
    #
    # Note: Nvfuser does not support interior (between-element) padding.
    #
    # Nvfuser errors
    # 1) Tensor arg and pad value must have the same dtype
    # 2) Number of pad widths must be at most twice the input dimension - NvFuser
    # 3) Dimension size after padding is not at least 0
    #
    # Jax and PyTorch errors
    # 1) Interior padding is non-negative
    # 2) Length of pad_widths is equal to number of operands

    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    input_shape = (2, 2)
    valid_pad_width = [1, 1, -1, 2]

    yield SampleInput(
        make_arg(input_shape),
        valid_pad_width,
        make_number(find_nonmatching_dtype(dtype)),
    ), RuntimeError, "Tensor arg and pad value must have the same dtype."

    # TODO Add better error message.
    # Dimension size after padding is not at least 0
    delete_all_pad_width = [-3, 0, 0, 0]
    yield SampleInput(
        make_arg(input_shape), delete_all_pad_width, make_number(dtype)
    ), RuntimeError, "extent_int > 0"

    too_many_pad_width = [1, 1, 1, 1, 1, 1]
    yield SampleInput(
        make_arg(input_shape), too_many_pad_width, make_number(dtype)
    ), RuntimeError, "Number of pad widths must be at most twice the input dimension"

    uneven_pad_width = [1, 1, 0]
    yield SampleInput(
        make_arg(input_shape), uneven_pad_width, make_number(dtype)
    ), RuntimeError, "Invalid number of padding widths"


def permute_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    cases = (
        ((4, 3, 7, 8), (0, 1, 2, 3)),
        ((4, 3, 7, 8), (1, -2, 0, 3)),
        ((4, 3, 7, 8), (-2, 1, 0, -1)),
        ((4, 3, 7, 8), (0, 3, 1, 2)),
        ((4, 3, 7, 8), (0, -1, 1, 2)),
        ((4, 7), (1, 0)),
    )

    for shape, dims in cases:
        yield SampleInput(make_arg(shape), dims)


def permute_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # torch.permute(input: torch.Tensor, dims: List[int])

    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    input_shape = (10, 3, 4, 4)
    # dims = dtype, duplicate, in-range

    # TODO Add dtype check.
    yield SampleInput(
        make_arg(input_shape), [0.0, 1.0, 2.0, 3.0]
    ), TypeError, "permute(): incompatible function arguments"

    # TODO Add duplicate axis check.
    yield SampleInput(
        make_arg(input_shape), [0, 1, 1, 3]
    ), RuntimeError, "Duplicate entries in transformation map"

    # TODO Add in-range axis check.
    yield SampleInput(
        make_arg(input_shape), [0, 1, 2, 4]
    ), RuntimeError, "New2Old axes are not within the number of dimensions of the provided domain"

    # TODO Add in-range axis check.
    yield SampleInput(
        make_arg(input_shape), [0, 1, 2, -5]
    ), RuntimeError, "New2Old axes are not within the number of dimensions of the provided domain"

    # TODO Add missing axes check.
    # If dims list is empty, NvFuser ignores the permute operation.
    yield SampleInput(
        make_arg(input_shape), [0]
    ), RuntimeError, "The number of dimensions in the tensor input does not match the length of the desired ordering of dimensions"

    # TODO Add out-of-bounds axes check.
    yield SampleInput(
        make_arg(input_shape), [0, 1, 2, 3, 4]
    ), RuntimeError, "The number of dimensions in the tensor input does not match the length of the desired ordering of dimensions"


def reduction_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor,
        device="cuda",
        dtype=dtype,
        requires_grad=requires_grad,
        # We set low (inclusive) and high (exclusive) here to avoid values
        # whose products can otherwise become extremely large
        low=-2,
        high=3,
    )

    # shape, dim, keepdim, dtype
    cases = (
        ((4, 4), None, False, None),
        ((5,), None, True, None),
        ((5,), (0,), False, None),
        ((8, 1, 6), (1,), True, None),
        ((8, 7, 5, 1), (0, 1), True, None),
        ((8, 7, 5, 1), (1, 3), False, None),
    )

    for c in cases:
        shape, dim, keepdim, dtype = c
        yield (SampleInput(make_arg(shape), dim, keepdim, dtype=dtype))


def reduction_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor,
        device="cuda",
        dtype=dtype,
        requires_grad=requires_grad,
        # We set low (inclusive) and high (exclusive) here to avoid values
        # whose products can otherwise become extremely large
        low=-2,
        high=3,
    )

    # shape
    cases = (
        (8, 1, 6),
        (8, 7, 5, 1),
    )

    # axes : List[int]
    # 1) all axis are int --- use float dtype
    # 2) all axes are unique --- duplicates
    # 3) after normalization, 0 <= axis[i] <= len(size)
    # 4) If empty tensor, then axis == 0

    int_dtype_axis = (
        lambda dims: float(dims),
        TypeError,
        "var_mean(): incompatible function arguments.",
    )
    duplicate_axis = (
        lambda dims: (0, 0, 0),
        RuntimeError,
        "Reduction axes are not unique",
    )
    lower_bound = (lambda dims: (-dims - 1,), RuntimeError, "Reduction on invalid axis")
    upper_bound = (lambda dims: (dims,), RuntimeError, "Reduction on invalid axis")
    # TODO Fix duplicate_axis, lower_bound, upper_bound
    error_cases = [int_dtype_axis]

    for shape, es in itertools.product(cases, error_cases):
        input_tensor = make_arg(shape)
        axis_fn, ex_type, ex_str = es
        yield SampleInput(input_tensor, axis_fn(len(shape))), ex_type, ex_str


def reshape_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    # TODO Add examples with negative index
    # TODO: Add zero-dim cases
    # TODO: Add strided tensor cases
    cases = (
        ((1, 19, 1, 12, 7, 1, 99), (1, 19, 1, 3, 2772)),
        ((3, 17, 80, 1), (51, 1, 2, 4, 10)),
        ((3, 17, 80, 1, 9), (51, 1, 2, 4, 10, 9)),
        ((2, 3, 4, 5), (1, 6, 1, 2, 2, 5)),
        ((22, 22, 2), (22, 11, 1, 1, 4)),
        ((37, 9, 7, 6, 10), (333, 2, 2, 3, 35)),
        ((8, 1, 1, 8, 1, 8), (8, 2, 4, 1, 8)),
        ((1, 333, 1), (1, 37, 9)),
        ((1, 333), (1, 1, 1, 111, 1, 3)),
        ((1, 27454, 1, 2), (1, 7844, 1, 7)),
        ((1, 7844, 1, 7), (1, 27454, 2)),
    )

    for tensor_shape, output_shape in cases:
        yield SampleInput(make_arg(tensor_shape), tensor_shape, output_shape)


def reshape_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # torch.reshape(input: Tensor, shape: [int])

    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    tensor_shape = (3, 14)

    # Only a single inferred axis -1.
    yield SampleInput(
        make_arg(tensor_shape), tensor_shape, [3, -1, -1]
    ), RuntimeError, "Only one dimension can by inferred"

    # Number of elements must be equal for input and output tensors
    yield SampleInput(
        make_arg(tensor_shape), tensor_shape, [3, 2, 8]
    ), RuntimeError, "Total element counts across view operation must match"


# TODO: add stride testing
def slice_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    # shape, start_indices, end_indices
    cases = (
        ((5, 7, 8), (1, 0, 3), (2, 6, 8)),
        ((3,), (1,), (2,)),
    )

    for shape, start_indices, end_indices in cases:
        a = make_arg(shape)
        yield SampleInput(a, start_indices=start_indices, end_indices=end_indices)


def slice_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    # shape
    cases = ((10, 10), (5, 5))

    check_start_indices = ErrorSample(
        {"start_indices": [-1, -2], "end_indices": [5, 5], "strides": [7, 7]},
        "Slice operation start_indices must be greater-than-or-equal-to 0.",
    )

    check_end_indices = ErrorSample(
        {"start_indices": [3, 4], "end_indices": [1, 2], "strides": [1, 1]},
        "Slice operation end_indices must be greater-than-or-equal-to start_indices.",
    )

    check_strides = ErrorSample(
        {"start_indices": [0, 0], "end_indices": [5, 5], "strides": [5, 5]},
        "nvFuser Limitation: All slice operation strides must be of size 1.",
    )

    check_tensor_dims = ErrorSample(
        {"start_indices": [0, 0, 0], "end_indices": [4, 4, 4], "strides": [1, 1, 1]},
        "Number of tensor dimensions does not match slice dimensions!",
    )

    check_slice_dims_start = ErrorSample(
        {"start_indices": [0, 0, 0], "end_indices": [4, 4], "strides": [1, 1]},
        "Slice start_indices and strides don't match!",
    )

    check_slice_dims_end = ErrorSample(
        {"start_indices": [0, 0], "end_indices": [4, 4, 4], "strides": [1, 1]},
        "Slice indexing attribute dimensions don't match!",
    )

    check_slice_dims_stride = ErrorSample(
        {"start_indices": [0, 0], "end_indices": [4, 4], "strides": [1, 1, 1]},
        "Slice start_indices and strides don't match!",
    )

    error_cases = [
        check_start_indices,
        check_end_indices,
        check_strides,
        check_tensor_dims,
        check_slice_dims_start,
        check_slice_dims_end,
        check_slice_dims_stride,
    ]

    for shape, es in itertools.product(cases, error_cases):
        input_tensor = make_arg(shape)
        yield SampleInput(input_tensor, **es.kwargs), es.ex_type, es.ex_str


def take_along_axis_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )
    make_index = partial(
        make_tensor, device="cuda", dtype=torch.long, requires_grad=False
    )

    # a.shape, dim, b.shape
    cases = (
        ((4, 2, 3), 0, (8, 2, 3)),
        ((4, 2, 3), 1, (4, 1, 3)),
        ((4, 2, 3), 2, (4, 2, 5)),
        ((4,), 0, (8)),
        ((4,), 0, (1)),
        ((4, 1), 0, (3, 1)),
        ((4, 1), 1, (4, 5)),
        # negative dim
        ((4, 2, 3), -3, (8, 2, 3)),
        ((4, 2, 3), -2, (4, 1, 3)),
        ((4, 2, 3), -1, (4, 2, 5)),
        ((4,), -1, (8)),
        ((4,), -1, (1)),
        ((4, 1), -2, (3, 1)),
        ((4, 1), -1, (4, 5)),
        # broadcast non-axis dimensions
        ((4, 2, 3), 0, (8, 2, 1)),
        ((4, 2, 3), 0, (8, 1, 3)),
        ((4, 2, 3), 0, (8, 2, 3)),
    )

    for shape_a, dim, shape_b in cases:
        a = make_arg(shape_a)
        b = make_index(shape_b, low=0, high=shape_a[dim])
        yield SampleInput(a, b, dim)


def take_along_axis_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # numpy.take_along_axis(arr: Tensor, indices: LongTensor, axis: int)
    #
    # torch.take_along_dim(input: Tensor, indices: LongTensor, dim: int)
    # * If no dim argument, flatten tensors.

    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )
    make_index = partial(
        make_tensor, device="cuda", dtype=torch.long, requires_grad=False
    )

    input_shape = (4, 2)
    a = make_arg(input_shape)

    valid_index_shape = (3, 1)
    b = make_index(valid_index_shape, low=0, high=10, dtype=torch.long)

    # out-of-bounds axis error checks
    ex_type = RuntimeError
    ex_str = "Tensor arguments have dimension"
    positive_error_dim = 2
    negative_error_dim = -3
    yield SampleInput(a, b, positive_error_dim), ex_type, ex_str
    yield SampleInput(a, b, negative_error_dim), ex_type, ex_str

    # TODO Fix: index tensor integer dtype
    # b = make_index(valid_index_shape, low=0, high=input_shape[0], dtype=torch.float)
    # yield SampleInput(a, b, 0), RuntimeError, "index tensor can only be int or long dtype."

    # TODO Fix: out-of-bound index value
    # b = make_index(valid_index_shape, low=10, high=100, dtype=torch.long)
    # yield SampleInput(a, b, 0), RuntimeError, "out of bounds index value."

    # TODO Fix: index shape exceeds input tensor axis
    # larger_index_shape = (5, 3)
    # b = make_index(
    #    larger_index_shape, low=0, high=larger_index_shape[0], dtype=torch.long
    # )
    # yield (
    #    SampleInput(a, b, 0),
    #    RuntimeError,
    #    "Expected dimension of index tensor to be smaller than input tensor except for specified axis",
    # )

    # TODO Fix: too many dimensions in index tensor
    # dim argument must be specified. Otherwise, the tensors are flattened.
    # too_many_dims_index_shape = (3, 1, 2)
    # b = make_index(
    #    too_many_dims_index_shape,
    #    low=0,
    #    high=too_many_dims_index_shape[0],
    #    dtype=torch.long,
    # )
    # yield (
    #    SampleInput(a, b, 0),
    #    RuntimeError,
    #    "input and indices should have the same number of dimensions",
    # )


def var_mean_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    """torch.var_mean(input, dim=None, *, correction=1, keepdim=False)"""
    correction = (0, 1)
    samples = reduction_generator(op, dtype, requires_grad)
    for c, sample in itertools.product(correction, samples):
        a = sample.args[0]
        dim = (
            sample.args[1]
            if (len(sample.args) > 1 and sample.args[1])
            else tuple(range(a.ndim))
        )
        keepdim = sample.args[2] if len(sample.args) > 2 else False
        yield SampleInput(a, dim, correction=c, keepdim=keepdim)


def where_error_generator(
    op: OpInfo, dtype: torch.dtype, requires_grad: bool = False, **kwargs
):
    # torch.where(condition, input, other)

    make_arg = partial(
        make_tensor, device="cuda", dtype=dtype, requires_grad=requires_grad
    )

    input_shape = (2, 3, 4)
    yield SampleInput(
        make_tensor(input_shape, device="cuda", dtype=torch.float32),
        make_arg(input_shape),
        make_arg(input_shape),
    ), RuntimeError, "Condition should be of DataType Bool"
