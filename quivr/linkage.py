from typing import Any, Generic, Iterator, Optional, TypeVar

import pyarrow as pa

from . import tables


class ArrowArrayIndex:
    """
    Represents an index over the values in a PyArrow Array.
    """

    index: dict[pa.Scalar, pa.UInt64Array]
    values: set[pa.Scalar]

    def __init__(self, array: pa.Array):
        self.index = {}
        self.values = set()

        if array.null_count > 0:
            raise ValueError("Array must not contain null values to be an index")

        in_progress_index: dict[pa.Scalar, list[pa.Scalar]] = {}
        for i in range(len(array)):
            val = array[i]
            if val in in_progress_index:
                in_progress_index[val].append(i)
            else:
                in_progress_index[val] = [i]
            self.values.add(val)

        for val, indices in in_progress_index.items():
            self.index[val] = pa.array(indices)

    def get(self, val: pa.Scalar) -> Optional[pa.UInt64Array]:
        return self.index.get(val, None)


LeftTable = TypeVar("LeftTable", bound=tables.Table)
RightTable = TypeVar("RightTable", bound=tables.Table)


class Linkage(Generic[LeftTable, RightTable]):
    """
    A Linkage is a mapping of rows across two Tables.

    The mapping is defined by a pair of arrays, one for each table, that
    contain common values.

    The Linkage can be used to iterate over all the unique values in the
    linkage columns, and to select the rows from each table that match a
    particular value.

    :param left_table: The left table in the linkage.
    :param right_table: The right table in the linkage.
    :param left_keys: The array of keys from the left table.
    :param right_keys: The array of keys from the right table.
    """

    left_table: LeftTable
    right_table: RightTable

    left_index: ArrowArrayIndex
    right_index: ArrowArrayIndex

    all_unique_values: set[pa.Scalar]

    def __init__(
        self,
        left_table: LeftTable,
        right_table: RightTable,
        left_keys: pa.Array,
        right_keys: pa.Array,
    ):
        """
        Create a new Linkage.

        The linkage is defined by the two tables, and the two arrays of keys.

        The keys must be the same length as the tables, and must not contain
        null values.
        """
        if left_keys.null_count > 0:
            raise ValueError("Left keys must not contain null values")
        if right_keys.null_count > 0:
            raise ValueError("Right keys must not contain null values")

        if len(left_keys) != len(left_table):
            raise ValueError("Left keys must have the same length as the left table")

        if len(right_keys) != len(right_table):
            raise ValueError("Right keys must have the same length as the right table")

        self.left_table = left_table
        self.right_table = right_table

        self.left_index = ArrowArrayIndex(left_keys)
        self.right_index = ArrowArrayIndex(right_keys)

        self.all_unique_values = self.left_index.values.union(self.right_index.values)

    def select_left(self, val: Any) -> LeftTable:
        """
        Select the rows from the left table that match the given value.

        If the value is not present in the left table, then an empty table is
        returned.
        """
        if not isinstance(val, pa.Scalar):
            val = pa.scalar(val)
        return self._select_left(val)

    def _select_left(self, val: pa.Scalar) -> LeftTable:
        left_indices = self.left_index.get(val)
        if left_indices is None:
            return self.left_table.empty()
        else:
            return self.left_table.take(left_indices)

    def select_right(self, val: Any) -> RightTable:
        """
        Select the rows from the right table that match the given value.

        If the value is not present in the right table, then an empty table is
        returned.
        """
        if not isinstance(val, pa.Scalar):
            val = pa.scalar(val)
        return self._select_right(val)

    def _select_right(self, val: pa.Scalar) -> RightTable:
        right_indices = self.right_index.get(val)
        if right_indices is None:
            return self.right_table.empty()
        else:
            return self.right_table.take(right_indices)

    def select(self, val: Any) -> tuple[LeftTable, RightTable]:
        """
        Select the rows from both tables that match the given value.

        If the value is not present in either table, then an empty table is
        returned for that table.
        """
        if not isinstance(val, pa.Scalar):
            val = pa.scalar(val)
        return self._select_left(val), self._select_right(val)

    def __getitem__(self, val: Any) -> tuple[LeftTable, RightTable]:
        return self.select(val)

    def iterate(self) -> Iterator[tuple[pa.Scalar, LeftTable, RightTable]]:
        """
        Returns an iterator over all the unique values in the linkage, and the rows from
        each table that match that value.
        """
        for val in self.all_unique_values:
            yield val, self._select_left(val), self._select_right(val)

    def __iter__(self) -> Iterator[tuple[pa.Scalar, LeftTable, RightTable]]:
        return self.iterate()

    def __len__(self) -> int:
        """Returns the number of unique values in the linkage."""
        return len(self.all_unique_values)


class MultiKeyLinkage(Linkage[LeftTable, RightTable]):
    """A MultiKeyLinkage links two tables using multiple arrays for
    composite key relationships.

    The linkage is defined by a pair of dictionaries, one for each
    table, which define the composite keys.

    The dictionaries must have the same keys, and the arrays must:
      - be identically typed under the same keys
      - have no null values
      - be the same length as the associated table

    Example:
        >>> from quivr import *
        >>> class Positions(Table):
        ...     x = Float32Column()
        ...     y = Float32Column()
        ...     time = TimestampColumn(unit="s")
        ...     id = UInt32Column()
        ...
        >>> class Velocities(tables.Table):
        ...     vx = Float32Column()
        ...     vy = Float32Column()
        ...     time = TimestampColumn(unit="s")
        ...     id = UInt32Column()
        ...
        >>> positions = Positions.from_data(
        ...     x=[0.0, 1.0, 2.0, 3.0, 4.0],
        ...     y=[0.0, 1.0, 2.0, 3.0, 4.0],
        ...     time=[0, 1, 2, 3, 4],
        ...     id=[0, 1, 1, 2, 2],
        ... )
        >>> velocities = Velocities.from_data(
        ...     vx=[0.0, 1.0, 2.0, 3.0, 4.0],
        ...     vy=[0.0, 1.0, 2.0, 3.0, 4.0],
        ...     time=[0, 1, 2, 3, 4],
        ...     id=[0, 1, 1, 2, 2],
        ... )
        >>> linkage = MultiKeyLinkage(
        ...     positions,
        ...     velocities,
        ...     {"id": positions.id, "time": positions.time},
        ...     {"id": velocities.id, "time": velocities.time},
        ... )
        >>> for val, left, right in linkage:
        ...     print(val, left, right)
        [('id', 0), ('time', datetime.datetime(1970, 1, 1, 0, 0))] Positions(size=1) Velocities(size=1)
        [('id', 2), ('time', datetime.datetime(1970, 1, 1, 0, 0, 4))] Positions(size=1) Velocities(size=1)
        [('id', 1), ('time', datetime.datetime(1970, 1, 1, 0, 0, 1))] Positions(size=1) Velocities(size=1)
        [('id', 2), ('time', datetime.datetime(1970, 1, 1, 0, 0, 3))] Positions(size=1) Velocities(size=1)
        [('id', 1), ('time', datetime.datetime(1970, 1, 1, 0, 0, 2))] Positions(size=1) Velocities(size=1)




    :param left_table: The left table to link.
    :param right_table: The right table to link.
    :param left_keys: A dictionary of key names to arrays of values. The arrays
        must be the same length as the left table, and must not contain null
        values. The key names must be the same as the right keys.
    :param right_keys: A dictionary of key names to arrays of values. The
        arrays must be the same length as the right table, and must not contain
        null values. The key names must be the same as the left keys.

    :raises ValueError: If the keys do not match the requirements above.

    """

    def __init__(
        self,
        left_table: LeftTable,
        right_table: RightTable,
        left_keys: dict[str, pa.Array],
        right_keys: dict[str, pa.Array],
    ):
        if set(left_keys.keys()) != set(right_keys.keys()):
            raise ValueError("Left and right key dictionaries must have the same keys")

        if len(left_keys) == 0:
            raise ValueError("Left and right key dictionaries must not be empty")

        self.dtypes = {}

        for k in left_keys.keys():
            left_array = left_keys[k]
            right_array = right_keys[k]
            if not isinstance(left_array, pa.Array):
                raise TypeError(f"Left key {k} must be an Arrow array")
            if not isinstance(right_array, pa.Array):
                raise TypeError(f"Right key {k} must be an Arrow array")

            if left_array.type != right_array.type:
                raise TypeError(
                    f"Left key {k} and right key {k} must have the same type; "
                    f"left={left_array.type}, right={right_array.type}"
                )

            if left_array.null_count > 0:
                raise ValueError(f"Left key {k} must not contain null values")
            if right_array.null_count > 0:
                raise ValueError(f"Right key {k} must not contain null values")

            if len(left_array) != len(left_table):
                raise ValueError(f"Left key {k} must have the same length as the left table")
            if len(right_array) != len(right_table):
                raise ValueError(f"Right key {k} must have the same length as the right table")

            self.dtypes[k] = left_array.type

        self.scalar_type = pa.struct(self.dtypes)

        left_structarray = _build_struct_array(left_keys)
        right_structarray = _build_struct_array(right_keys)

        super().__init__(left_table, right_table, left_structarray, right_structarray)

    def key(self, **kwargs: Any) -> pa.Scalar:
        """
        Returns a composite key scalar for the given values.

        Example:
            >>> from quivr import *
            >>> class Positions(Table):
            ...     x = Float32Column()
            ...     y = Float32Column()
            ...     time = TimestampColumn(unit="s")
            ...     id = UInt32Column()
            ...
            >>> class Velocities(tables.Table):
            ...     vx = Float32Column()
            ...     vy = Float32Column()
            ...     time = TimestampColumn(unit="s")
            ...     id = UInt32Column()
            ...
            >>> positions = Positions.from_data(
            ...     x=[0.0, 1.0, 2.0, 3.0, 4.0],
            ...     y=[0.0, 1.0, 2.0, 3.0, 4.0],
            ...     time=[0, 1, 2, 3, 4],
            ...     id=[0, 1, 1, 2, 2],
            ... )
            >>> velocities = Velocities.from_data(
            ...     vx=[0.0, 1.0, 2.0, 3.0, 4.0],
            ...     vy=[0.0, 1.0, 2.0, 3.0, 4.0],
            ...     time=[0, 1, 2, 3, 4],
            ...     id=[0, 1, 1, 2, 2],
            ... )
            >>> linkage = MultiKeyLinkage(
            ...     positions,
            ...     velocities,
            ...     {"time": positions.time, "id": positions.id},
            ...     {"time": velocities.time, "id": velocities.id},
            ... )
            >>> key = linkage.key(time=1, id=1)
            >>> key
            <pyarrow.StructScalar: [('time', datetime.datetime(1970, 1, 1, 0, 0, 1)), ('id', 1)]>
            >>> linkage[key]
            (Positions(size=1), Velocities(size=1))


        :param kwargs: The values for the composite key.
        :raises ValueError: If the keys do not match the linkage keys.
        """
        if set(kwargs.keys()) != set(self.dtypes.keys()):
            raise ValueError(f"Keys must match the linkage keys ({self.dtypes.keys()})")
        return pa.scalar(kwargs, type=self.scalar_type)


def _build_struct_array(keys: dict[str, pa.Array]) -> pa.Array:
    """
    Create a composite array from a list of arrays.
    """
    fields = []
    arrays = []
    for k, v in keys.items():
        fields.append(pa.field(k, v.type))
        arrays.append(v)
    return pa.StructArray.from_arrays(arrays, fields=fields)
