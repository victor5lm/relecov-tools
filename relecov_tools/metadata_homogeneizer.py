#!/usr/bin/env python

# Imports
import os
import sys
import json
import logging
import pandas as pd
import rich.console
import relecov_tools.utils
from relecov_tools.config_json import ConfigJson

log = logging.getLogger(__name__)
stderr = rich.console.Console(
    stderr=True,
    style="dim",
    highlight=False,
    force_terminal=relecov_tools.utils.rich_force_colors(),
)


def check_extension(instring, extensions):
    """Given a file as a string and a list of possible extensions,
    returns true if the extension can be found in the file"""
    for extension in extensions:
        if instring.endswith(extension):
            return True


def open_json(json_path):
    """Load the json file"""
    with open(json_path) as file:
        json_dict = json.load(file)
    return json_dict


class Homogeneizer:
    """Homogeneizer object"""

    def __init__(self, filename):
        self.filename = filename
        self.dictionary_path = None
        self.dicionary = None
        self.centre = None
        self.dataframe = None

        # To Do: replace string with local file system for testing
        # Header path can be found in conf/configuration.json
        config_json = ConfigJson()
        self.header = config_json.get_configuration("new_table_headers")
        self.translated_dataframe = pd.DataFrame(columns=self.header)
        return

    def associate_dict(self):
        """Detect the origin centre of the metadata, and finds the corresponding json file to use"""

        # Check name of the file attribute of the object
        # Check schema with all centres and find their json
        # associate centre and json with object
        # raise error when in doubt
        # must check on schema/institution_schemas

        config_json = ConfigJson(json_file=os.path.join(os.path.dirname(__file__), "schema", "institution_to_schema.json"))
        institution_dict = config_json.json_data
        # path_to_institution_json = "Schemas/institution_to_schema.json"

        detected = []

        for key in institution_dict.keys():
            # cap insensitive
            if key.lower() in self.filename.split("/")[-1].lower():
                detected.append(institution_dict[key])

        if len(set(detected)) == 0:
            print(
                f"No file could be found matching with the '{self.filename}' filename given."
            )

        elif len(set(detected)) > 1:
            print("The following matches were identified:")
            print(*set(detected), sep="\n")  # change this to an elegant form
            sys.exit()  # maybe check which ones are being mixed or when none is being found
        else:
            self.dictionary_path = detected[0]  # first item, they are all equal
            print(
                f"JSON file found successfully: {self.dictionary_path}"
            )  # delete this after testing

        return

    def load_dataframe(self):
        """Detect possible extensions for the metadata file
        Open it into a dataframe"""

        excel_extensions = [".xlsx", ".xls", ".xlsm", ".xlsb"]
        odf_extension = [".odf"]
        csv_extensions = [".csv"]
        tsv_extensions = [".tsv"]

        if check_extension(self.filename, excel_extensions):
            self.dataframe = pd.read_excel(self.filename, header=0)
        elif check_extension(self.filename, odf_extension):
            # Needs a special package
            self.dataframe = pd.read_excel(self.filename, engine="odf", header=0)
        elif check_extension(self.filename, csv_extensions):
            self.dataframe = pd.read_csv(self.filename, sep=",", header=0)
        elif check_extension(self.filename, tsv_extensions):
            self.dataframe = pd.read_csv(self.filename, sep="\t", header=0)
        else:
            print(
                f"The extension of the file '{self.filename}' could not be identified. My bad there."
            )

        return

    def load_dictionary(self):
        """Load the corresponding dictionary"""

        # To Do: replace string with local file system for testing
        config_json = ConfigJson(json_file=os.path.join(os.path.dirname(__file__), "schema", "institution_to_schema.json"))
        institution_dict = config_json.json_data
        path_to_tools = ""
        dict_path = os.join()path_to_tools + "schema/" + self.dictionary_path
        self.dictionary = open_json(dict_path)
        return

    def translate_dataframe(self):
        """Use the corresponding dictionary to translate the df"""
        # if dictionary is "none" or similar, do nothing

        for key, value in self.dictionary["equivalence"].items():
            if len(value) == 0:
                print(
                    f"Found empty equivalence in the '{self.dictionary_path}' schema: '{key}'"
                )
            elif value in self.dataframe.columns:
                self.translated_dataframe[key] = self.dataframe[value]
            else:
                print(
                    f"Column '{value}' indicated in the '{self.dictionary_path}' schema could not be found."
                )

        for key, value in self.dictionary["constants"].items():
            if key in self.translated_dataframe.columns:
                self.translated_dataframe[key] = value
            else:
                print(
                    f"Value '{key}' in the '{self.dictionary_path}' schema not found in the resulting dataframe"
                )

        # Nightmare
        for key, value in self.dictionary["outer"].items():
            pass

        return

    def verify_translated_dataframe(self):
        """Checks if the dataframe holds all the needed values for the relecov tools suite"""

        if self.dataframe.shape[0] != self.translated_dataframe.shape[0]:
            print("Different number of rows after translation")
        else:
            print("Number of rows: OK")

        # search for missing values
        missing_values = list(set(self.header) - set(self.translated_dataframe.columns))
        if len(missing_values) > 0:
            print("Found the following missing values during translated table validation:")
            print(*missing_values, sep="\n")

        # search for extra values
        extra_values = list(set(self.translated_dataframe.columns) - set(self.header))
        if len(extra_values) > 0:
            print("Found the following extra values during translated table validation:")
            print(*extra_values, sep="\n")

        return

    def export_translated_dataframe(self):
        # 
        pass
        return
