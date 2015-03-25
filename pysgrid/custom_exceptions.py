'''
Created on Mar 23, 2015

@author: ayan
'''


class CannotFindPadding(Exception):
    
    base_message = 'The netCDF file appears to have conform to SGRID conventions, but padding values cannot be found.'
        
    def __str__(self):
        error_message = self.base_message.format(self.padding_str)
        return error_message


class SGridNonCompliant(Exception):
    
    base_message = 'This netCDF object does not appear to be SGRID compliant: {0}.'
    
    def __init__(self, dataset_obj):
        self.dataset_obj = dataset_obj
        
    def __str__(self):
        error_message = self.base_message.format(self.dataset_obj)
        return error_message