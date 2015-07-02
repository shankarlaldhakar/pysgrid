# -*- coding: utf-8 -*-
import bisect
import os
import glob
from urlparse import urlparse


from django.db import models
from typedmodels import TypedModel
from jsonfield import JSONField

import netCDF4 as nc4
from pyaxiom.netcdf import EnhancedDataset, EnhancedMFDataset

from wms.models import VirtualLayer, Layer, Style
from django.conf import settings
from django.http.response import HttpResponse
from autoslug import AutoSlugField

from wms.utils import DotDict, find_appropriate_time
from wms.data_handler import blank_canvas


class Dataset(TypedModel):
    uri = models.CharField(max_length=1000)
    name = models.CharField(max_length=200, unique=True, help_text="Name/ID to use. No special characters or spaces ('_','0123456789' and A-Z are allowed).")
    title = models.CharField(max_length=200, help_text="Human Readable Title")
    abstract = models.CharField(max_length=2000, help_text="Short Description of Dataset")
    keep_up_to_date = models.BooleanField(help_text="Check this box to keep the dataset up-to-date if changes are made to it on disk or remote server.", default=True)
    display_all_timesteps = models.BooleanField(help_text="Check this box to display each time step in the GetCapabilities document, instead of just the range that the data spans.)", default=False)
    cache_last_updated = models.DateTimeField(null=True, editable=False)
    json = JSONField(blank=True, null=True, help_text="Arbitrary dataset-specific json blob")
    slug = AutoSlugField(populate_from='name')

    def __init__(self, *args, **kwargs):
        super(Dataset, self).__init__(*args, **kwargs)
        self.canon_dataset = self.netcdf4_dataset()

    def __del__(self):
        try:
            self.canon_dataset.close()
        except (RuntimeError, AttributeError):
            pass

    def __unicode__(self):
        return self.name

    @classmethod
    def identify(cls, uri):
        def all_subclasses(klass):
            return klass.__subclasses__() + [g for s in klass.__subclasses__()
                                             for g in all_subclasses(s)]
        for x in all_subclasses(cls):
            try:
                if x.is_valid(uri) is True:
                    return x
            except AttributeError:
                pass

    def path(self):
        if urlparse(self.uri).scheme == "" and not self.uri.startswith("/"):
            # We have a relative path, make it absolute to the sciwms directory.
            return str(os.path.realpath(os.path.join(settings.PROJECT_ROOT, self.uri)))
        else:
            return str(self.uri)

    def netcdf4_dataset(self):
        try:
            return EnhancedDataset(self.path())
        except:
            try:
                return EnhancedMFDataset(self.path(), aggdim='time')
            except:
                return None

    def topology_dataset(self):
        try:
            return EnhancedDataset(self.topology_file)
        except RuntimeError:
            return None

    def getmap(self, layer, request):
        raise NotImplementedError

    def getlegendgraphic(self, layer, request):
        raise NotImplementedError

    def getfeatureinfo(self, layer, request):
        raise NotImplementedError

    def empty_response(self, layer, request, content_type=None):
        """ Abstracted here to support many different empty response types"""
        content_type = content_type or 'image/png'
        if content_type == 'image/png':
            width = request.GET['width']
            height = request.GET['height']
            canvas = blank_canvas(width, height)
            response = HttpResponse(content_type=content_type)
            canvas.print_png(response)
        return response

    def wgs84_bounds(self, layer):
        raise NotImplementedError

    def time_bounds(self, layer):
        times = self.times(layer)
        try:
            return DotDict(min=times[0], max=times[-1])
        except IndexError:
            return DotDict(min=None, max=None)

    def depth_bounds(self, layer):
        depths = self.depths(layer)
        try:
            return DotDict(min=depths[0], max=depths[-1])
        except IndexError:
            return DotDict(min=None, max=None)

    def depths(self, layer):
        raise NotImplementedError

    def has_cache(self):
        return os.path.exists(self.topology_file)

    def update_cache(self, force=False):
        raise NotImplementedError

    def clear_cache(self):
        cache_file_list = glob.glob(os.path.join(settings.TOPOLOGY_PATH, self.safe_filename + '*'))
        for cache_file in cache_file_list:
            os.remove(cache_file)

    def analyze_virtual_layers(self):
        nc = self.netcdf4_dataset()
        if nc is not None:
            # Earth Projected Sea Water Velocity
            u_names = ['eastward_sea_water_velocity', 'eastward_sea_water_velocity_assuming_no_tide']
            v_names = ['northward_sea_water_velocity', 'northward_sea_water_velocity_assuming_no_tide']
            us = nc.get_variables_by_attributes(standard_name=lambda v: v in u_names)
            vs = nc.get_variables_by_attributes(standard_name=lambda v: v in v_names)
            VirtualLayer.make_vector_layer(us, vs, 'sea_water_velocity', 'vectors', self.id)

            # Grid projected Sea Water Velocity
            u_names = ['x_sea_water_velocity', 'grid_eastward_sea_water_velocity']
            v_names = ['y_sea_water_velocity', 'grid_northward_sea_water_velocity']
            us = nc.get_variables_by_attributes(standard_name=lambda v: v in u_names)
            vs = nc.get_variables_by_attributes(standard_name=lambda v: v in v_names)
            VirtualLayer.make_vector_layer(us, vs, 'grid_sea_water_velocity', 'vectors', self.id)

            # Earth projected Winds
            u_names = ['eastward_wind']
            v_names = ['northward_wind']
            us = nc.get_variables_by_attributes(standard_name=lambda v: v in u_names)
            vs = nc.get_variables_by_attributes(standard_name=lambda v: v in v_names)
            VirtualLayer.make_vector_layer(us, vs, 'winds', 'barbs', self.id)

            # Grid projected Winds
            u_names = ['x_wind', 'grid_eastward_wind']
            v_names = ['y_wind', 'grid_northward_wind']
            us = nc.get_variables_by_attributes(standard_name=lambda v: v in u_names)
            vs = nc.get_variables_by_attributes(standard_name=lambda v: v in v_names)
            VirtualLayer.make_vector_layer(us, vs, 'grid_winds', 'barbs', self.id)

            # Earth projected Ice velocity
            u_names = ['eastward_sea_ice_velocity']
            v_names = ['northward_sea_ice_velocity']
            us = nc.get_variables_by_attributes(standard_name=lambda v: v in u_names)
            vs = nc.get_variables_by_attributes(standard_name=lambda v: v in v_names)
            VirtualLayer.make_vector_layer(us, vs, 'sea_ice_velocity', 'vectors', self.id)

            nc.close()

    def process_layers(self):
        nc = self.netcdf4_dataset()
        if nc is not None:

            for v in nc.variables:
                l, _ = Layer.objects.get_or_create(dataset_id=self.id, var_name=v)

                nc_var = nc.variables[v]
                if hasattr(nc_var, 'valid_range'):
                    l.default_min = nc_var.valid_range[0]
                    l.default_max = nc_var.valid_range[-1]
                # valid_min and valid_max take presendence
                if hasattr(nc_var, 'valid_min'):
                    l.default_min = nc_var.valid_min
                if hasattr(nc_var, 'valid_max'):
                    l.default_max = nc_var.valid_max

                if hasattr(nc_var, 'standard_name'):
                    std_name = nc_var.standard_name
                    l.std_name = std_name

                    if len(nc_var.dimensions) > 1:
                        l.active = True

                if hasattr(nc_var, 'long_name'):
                    l.description = nc_var.long_name

                # Set some standard styles
                l.styles = Style.defaults()
                l.save()

            nc.close()

        self.analyze_virtual_layers()

    def nearest_time(self, layer, time):
        """
        Return the time index and time value that is closest
        """
        nc = self.canon_dataset
        time_vars = nc.get_variables_by_attributes(standard_name='time')
        if len(time_vars) == 1:
            time_var = time_vars[0]
        else:
            # if there is more than variable with standard_name = time
            # fine the appropriate one to use with the layer
            var_obj = nc.variables[layer.access_name]
            time_var_name = find_appropriate_time(var_obj, time_vars)
            time_var = nc.variables[time_var_name]
        units = time_var.units
        if hasattr(time_var, 'calendar'):
            calendar = time_var.calendar
        else:
            calendar = 'gregorian'
        num_date = round(nc4.date2num(time, units=units, calendar=calendar))

        times = time_var[:]
        time_index = bisect.bisect_right(times, num_date)
        try:
            times[time_index]
        except IndexError:
            time_index -= 1
        return time_index, times[time_index]

    def active_layers(self):
        layers = self.layer_set.prefetch_related('styles').filter(active=True)
        vlayers = self.virtuallayer_set.prefetch_related('styles').filter(active=True)
        return list(layers) + list(vlayers)

    def all_layers(self):
        layers = self.layer_set.prefetch_related('styles').all()
        vlayers = self.virtuallayer_set.prefetch_related('styles').all()
        return sorted(list(layers) + list(vlayers), key=lambda x: x.active, reverse=True)

    @property
    def safe_filename(self):
        return "".join(c for c in self.name if c.isalnum()).rstrip()

    @property
    def topology_file(self):
        return os.path.join(settings.TOPOLOGY_PATH, '{}.nc'.format(self.safe_filename))

    @property
    def domain_file(self):
        return os.path.join(settings.TOPOLOGY_PATH, '{}.domain'.format(self.safe_filename))

    @property
    def node_tree_root(self):
        return os.path.join(settings.TOPOLOGY_PATH, '{}.tree').format(self.safe_filename)

    @property
    def node_tree_data_file(self):
        return '{}.dat'.format(self.node_tree_root)

    @property
    def node_tree_index_file(self):
        return '{}.idx'.format(self.node_tree_root)

    @property
    def cell_tree_file(self):
        return os.path.join(settings.TOPOLOGY_PATH, '{}.cells').format(self.safe_filename)

    @property
    def cell_tree_index_file(self):
        return '{}.idx'.format(self.cell_tree_root)

    @property
    def cell_tree_data_file(self):
        return '{}.dat'.format(self.cell_tree_root)

    @property
    def online(self):
        return urlparse(self.uri).scheme != ""

    def humanize(self):
        return "Generic Dataset"