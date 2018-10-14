#
# Copyright 2017-2018 European Centre for Medium-Range Weather Forecasts (ECMWF).
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Authors:
#   Alessandro Amici - B-Open - https://bopen.eu
#

from __future__ import absolute_import, division, print_function

import numpy as np

from xarray import Variable
from xarray.core import indexing
from xarray.core.utils import Frozen, FrozenOrderedDict
from xarray.backends.common import AbstractDataStore, BackendArray
from xarray.backends.file_manager import CachingFileManager
from xarray.backends.locks import ensure_lock, SerializableLock

# FIXME: Add a dedicated lock just in case, even if ecCodes is supposed to be thread-safe in most
# circumstances. See: https://confluence.ecmwf.int/display/ECC/Frequently+Asked+Questions
ECCODES_LOCK = SerializableLock()


class CfGribArrayWrapper(BackendArray):
    def __init__(self, datastore, array):
        self.datastore = datastore
        self.shape = array.shape
        self.dtype = array.dtype
        self.array = array

    def __getitem__(self, key):
        return indexing.explicit_indexing_adapter(
            key, self.shape, indexing.IndexingSupport.BASIC, self._getitem)

    def _getitem(self, key):
        with self.datastore.lock:
            return self.array[key]


class CfGribDataStore(AbstractDataStore):
    """
    Implements the ``xr.AbstractDataStore`` read-only API for a GRIB file.
    """
    def __init__(self, filename, lock=None, **backend_kwargs):
        import cfgrib
        if lock is None:
            lock = ECCODES_LOCK
        self.lock = ensure_lock(lock)
        backend_kwargs['filter_by_keys'] = tuple(backend_kwargs.get('filter_by_keys', {}).items())
        self._manager = CachingFileManager(
            cfgrib.open_file, filename, lock=lock, mode='r', kwargs=backend_kwargs)

    @classmethod
    def from_path(cls, *args, **kwargs):
        return cls(*args, **kwargs)

    @property
    def ds(self):
        return self._manager.acquire()

    def open_store_variable(self, name, var):
        if isinstance(var.data, np.ndarray):
            data = var.data
        else:
            data = indexing.LazilyOuterIndexedArray(CfGribArrayWrapper(self, var.data))

        encoding = self.ds.encoding.copy()
        encoding['original_shape'] = var.data.shape

        return Variable(var.dimensions, data, var.attributes, encoding)

    def get_variables(self):
        return FrozenOrderedDict((k, self.open_store_variable(k, v))
                                 for k, v in self.ds.variables.items())

    def get_attrs(self):
        return Frozen(self.ds.attributes)

    def get_dimensions(self):
        return Frozen(self.ds.dimensions)

    def get_encoding(self):
        encoding = {}
        encoding['unlimited_dims'] = {k for k, v in self.ds.dimensions.items() if v is None}
        return encoding

    def close(self):
        self._manager.close()