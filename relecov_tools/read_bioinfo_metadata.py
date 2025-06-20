#!/usr/bin/env python
import os
import sys
import rich.console
import re
import shutil
import numpy as np
from datetime import datetime
from rich.prompt import Prompt

import pandas as pd
import relecov_tools.utils
from relecov_tools.config_json import ConfigJson
from relecov_tools.base_module import BaseModule

stderr = rich.console.Console(
    stderr=True,
    style="dim",
    highlight=False,
    force_terminal=relecov_tools.utils.rich_force_colors(),
)


class BioinfoReportLog:
    def __init__(self, log_report=None, output_dir="/tmp/"):
        if not log_report:
            self.report = {"error": {}, "valid": {}, "warning": {}}
        else:
            self.report = log_report

    def update_log_report(self, method_name, status, message):
        """Update the progress log report with the given method name, status, and message.

        Args:
            method_name (str): The name of the method being logged.
            status (str): The status of the log message, can be one of 'valid', 'error', or 'warning'.
            message (str): The message to be logged.

        Returns:
            dict: The updated progress log report.

        Raises:
            ValueError: If an invalid status is provided.
        """
        if status == "valid":
            self.report["valid"].setdefault(method_name, []).append(message)
            return self.report
        elif status == "error":
            self.report["error"].setdefault(method_name, []).append(message)
            return self.report
        elif status == "warning":
            self.report["warning"].setdefault(method_name, []).append(message)
            return self.report
        else:
            raise ValueError("Invalid status provided.")

    def print_log_report(self, name, sections):
        """Prints the log report by calling util's function.

        Args:
            name (str): The name of the log report.
            sections (list of str): The sections of the log report to be printed.

        Returns:
            None
        """
        relecov_tools.utils.print_log_report(self.report, name, sections)


# TODO: Add method to validate bioinfo_config.json file requirements.
class BioinfoMetadata(BaseModule):
    def __init__(
        self,
        json_file=None,
        json_schema_file=None,
        input_folder=None,
        output_dir=None,
        software_name=None,
        update=False,
        **kwargs,
    ):
        super().__init__(output_dir=output_dir, called_module=__name__)
        config_json = ConfigJson()
        self.log.info("Initiating read-bioinfo-metadata process")
        # Init process log

        if json_schema_file is None:
            schema_name = config_json.get_topic_data("json_schemas", "relecov_schema")
            json_schema_file = os.path.join(
                os.path.dirname(os.path.realpath(__file__)), "schema", schema_name
            )

        self.json_schema = relecov_tools.utils.read_json_file(json_schema_file)

        if output_dir is None:
            self.output_dir = relecov_tools.utils.prompt_path(
                msg="Select the output folder"
            )
        else:
            self.output_dir = os.path.realpath(output_dir)
        self.logsum = self.parent_log_summary(output_dir=output_dir)
        self.log_report = BioinfoReportLog(output_dir=output_dir)

        # Parse read-lab-meta-data
        if json_file is None:
            json_file = relecov_tools.utils.prompt_path(
                msg="Select the json file that was created by the read-lab-metadata"
            )
        if not os.path.isfile(json_file):
            self.update_all_logs(
                self.__init__.__name__,
                "error",
                f"file {json_file} does not exist",
            )
            sys.exit(
                self.log_report.print_log_report(self.__init__.__name__, ["error"])
            )

        #  Assign a new name for better readability
        self.readlabmeta_json_file = json_file

        # Initialize j_data object
        stderr.print("[blue]Reading lab metadata json")
        self.j_data = self.collect_info_from_lab_json()
        batch_id = self.get_batch_id_from_data(self.j_data)
        self.set_batch_id(batch_id)

        meta_basename = os.path.basename(self.readlabmeta_json_file).split(".")[0]
        if batch_id in meta_basename:
            # File is probably tagged already from a previous process
            self.out_filename = "bioinfo_" + meta_basename + ".json"
        else:
            self.out_filename = self.tag_filename("bioinfo_" + meta_basename + ".json")
        self.update = update

        # Parse input/output folder
        if input_folder is None:
            self.input_folder = relecov_tools.utils.prompt_path(
                msg="Select the input folder"
            )
        else:
            self.input_folder = input_folder

        # Parse bioinfo configuration
        self.bioinfo_json_file = os.path.join(
            os.path.dirname(__file__), "conf", "bioinfo_config.json"
        )
        if software_name is None:
            software_name = relecov_tools.utils.prompt_path(
                msg="Select the software, pipeline or tool use in the bioinformatic analysis: "
            )
        self.software_name = software_name
        available_software = relecov_tools.utils.get_available_software(
            self.bioinfo_json_file
        )
        bioinfo_config = ConfigJson(self.bioinfo_json_file)

        self.schema_path = os.path.join(
            os.path.dirname(__file__), "schema", "relecov_schema.json"
        )
        self.bioinfo_schema = relecov_tools.utils.load_schema(self.schema_path)

        if self.software_name in available_software:
            self.software_config = bioinfo_config.get_configuration(self.software_name)
        else:
            self.update_all_logs(
                self.__init__.__name__,
                "error",
                f"No configuration available for '{self.software_name}'. Currently, the only available software options are:: {', '.join(available_software)}",
            )
            sys.exit(
                self.log_report.print_log_report(self.__init__.__name__, ["error"])
            )

    def update_all_logs(self, method_name, status, message):
        report = self.log_report.update_log_report(method_name, status, message)
        if status == "error":
            self.logsum.add_error(key=method_name, entry=message)
        elif status == "warning":
            self.logsum.add_warning(key=method_name, entry=message)
        return report

    def scann_directory(self):
        """Scanns bioinfo analysis directory and identifies files according to the file name patterns defined in the software configuration json.

        Returns:
            files_found: A dictionary containing file paths found based on the definitions provided in the bioinformatic JSON file within the software scope (self.software_config).
        """
        method_name = f"{self.scann_directory.__name__}"
        total_files = 0
        files_found = {}
        all_scanned_things = []
        for root, dirs, files in os.walk(self.input_folder, topdown=True):
            # Skipping nextflow's work junk directory
            dirs[:] = [d for d in dirs if "work" not in root.split("/")]
            total_files = total_files + len(files)
            all_scanned_things.append((root, files))
        for topic_key, topic_scope in self.software_config.items():
            if "fn" not in topic_scope:  # try/except fn
                self.update_all_logs(
                    method_name,
                    "warning",
                    f"No 'fn' (file pattern) found in '{self.software_name}.{topic_key}'.",
                )
                continue
            for tup in all_scanned_things:
                matching_files = [
                    os.path.join(tup[0], file_name)
                    for file_name in tup[1]
                    if re.search(topic_scope["fn"], os.path.join(tup[0], file_name))
                ]
                if matching_files:
                    if topic_key not in files_found:
                        files_found[topic_key] = []
                    files_found[topic_key].extend(matching_files)
        if len(files_found) < 1:
            self.update_all_logs(
                method_name,
                "error",
                f"No files found in '{self.input_folder}' according to '{os.path.basename(self.bioinfo_json_file)}' file name patterns.",
            )
            sys.exit(self.log_report.print_log_report(method_name, ["error"]))
        else:
            self.update_all_logs(
                self.scann_directory.__name__,
                "valid",
                f"Scanning process succeed. Scanned {total_files} files.",
            )
            self.log_report.print_log_report(method_name, ["valid", "warning"])
            return files_found

    def mapping_over_table(self, j_data, map_data, mapping_fields, table_name):
        """Maps bioinformatics metadata from map_data to j_data based on the mapping_fields.
        Args:
            j_data (list(dict{str:str}): A list of dictionaries containing metadata lab (one item per sample).
            map_data (dict(dict{str:str})): A dictionary containing bioinfo metadata handled by the method handling_files().
            mapping_fields (dict{str:str}): A dictionary of mapping fields defined in the 'content' definition under each software scope (see conf/bioinfo.config).
            table_name (str): Path to the mapping file/table.
        Returns:
            j_data: updated j_data with bioinformatic metadata mapped in it.
        """
        method_name = f"{self.mapping_over_table.__name__}:{self.software_name}.{self.current_config_key}"
        errors = []
        field_errors = {}
        field_valid = {}

        sample_ids = {
            row.get("sequencing_sample_id")
            for row in j_data
            if row.get("sequencing_sample_id")
        }
        matched_samples = sample_ids & set(map_data.keys())

        if not matched_samples:
            no_samples = True
        else:
            no_samples = False

        for row in j_data:
            # TODO: We should consider an independent module that verifies that sample's name matches this pattern.
            #       If we add warnings within this module, every time mapping_over_table is invoked it will print redundant warings
            if not row.get("sequencing_sample_id"):
                self.update_all_logs(
                    method_name,
                    "warning",
                    f'Sequencing_sample_id missing in {row.get("collecting_sample_id")}... Skipping...',
                )
                continue
            sample_name = row["sequencing_sample_id"]
            if sample_name in map_data.keys():
                for field, value in mapping_fields.items():
                    try:
                        raw_val = map_data[sample_name][value]
                        raw_val = self.replace_na_value_if_needed(field, raw_val)
                        expected_type = (
                            self.bioinfo_schema["properties"]
                            .get(field, {})
                            .get("type", "string")
                        )
                        row[field] = relecov_tools.utils.cast_value_to_schema_type(
                            raw_val, expected_type
                        )
                        field_valid[sample_name] = {field: value}
                    except KeyError as e:
                        field_errors[sample_name] = {field: e}
                        row[field] = "Not Provided [SNOMED:434941000124101]"
                        continue
            elif (
                not self.software_config[self.current_config_key].get(
                    "multiple_samples"
                )
                and no_samples is True
            ):  # When sample ID is not in mapping_fields because is not a multiple_sample table
                for field, value_dict in mapping_fields.items():
                    for json_field, software_key in value_dict.items():
                        try:
                            raw_val = map_data[software_key][field]
                            expected_type = (
                                self.bioinfo_schema["properties"]
                                .get(json_field, {})
                                .get("type", "string")
                            )

                            row[json_field] = (
                                relecov_tools.utils.cast_value_to_schema_type(
                                    raw_val, expected_type
                                )
                            )
                            field_valid[software_key] = {json_field: field}
                        except KeyError as e:
                            field_errors[software_key] = {json_field: str(e)}
                            row[json_field] = "Not Provided [SNOMED:434941000124101]"
            else:
                errors.append(sample_name)
                for field in mapping_fields.keys():
                    row[field] = "Not Provided [SNOMED:434941000124101]"
        # work around when map_data comes from several per-sample tables/files instead of single table
        if len(table_name) > 2:
            table_name = os.path.dirname(table_name[0])
        else:
            table_name = table_name[0]
        # Parse missing sample errors
        if errors:
            lenerrs = len(errors)
            self.update_all_logs(
                method_name,
                "warning",
                f"{lenerrs} samples missing in '{table_name}': {', '.join(errors)}.",
            )
        else:
            self.update_all_logs(
                method_name,
                "valid",
                f"All samples were successfully found in {table_name}.",
            )
        # Parse missing fields errors
        # TODO: this stdout can be improved
        if len(field_errors) > 0:
            self.update_all_logs(
                method_name,
                "warning",
                f"Missing fields in {table_name}:\n\t{field_errors}",
            )
        else:
            self.update_all_logs(
                method_name,
                "valid",
                f"Successfully mapped fields in {', '.join(field_valid.keys())}.",
            )
        # Print report
        self.log_report.print_log_report(method_name, ["valid", "warning"])
        return j_data

    def validate_samplenames(self):
        """Validate that the sequencing_sample_id from the JSON input is present in the samples_id.txt.

        Raises:
            ValueError: If no sample from the JSON input matches the samples in the samples_id.txt.
        """
        samplesid_path = os.path.join(self.input_folder, "samples_id.txt")
        with open(samplesid_path, "r") as file:
            samplesid_list = [line.strip() for line in file.readlines()]
        json_samples = [sample["sequencing_sample_id"] for sample in self.j_data]
        matching_samples = set(json_samples).intersection(samplesid_list)
        if not matching_samples:
            raise ValueError(
                "No sample from the JSON input matches the samples in the provided analysis folder."
            )
        else:
            print(
                f"Found {len(matching_samples)}/{len(json_samples)} matching samples in the samplesheet."
            )
            self.log.info(
                f"Found {len(matching_samples)}/{len(json_samples)} matching samples in the samplesheet."
            )

    def validate_software_mandatory_files(self, files_dict):
        """Validates the presence of all mandatory files as defined in the software configuration JSON.

        Args:
            files_dict (dict{str:str}): A dictionary containing file paths found based on the definitions provided in the bioinformatic JSON file within the software scope (self.software_config).
        """
        method_name = f"{self.validate_software_mandatory_files.__name__}"
        missing_required = []
        for key in self.software_config:
            if key == "fixed_values":
                continue
            if self.software_config[key].get("required") is True:
                try:
                    files_dict[key]
                except KeyError:
                    missing_required.append(key)
                    continue
            else:
                continue
        if len(missing_required) >= 1:
            self.update_all_logs(
                method_name,
                "error",
                f"Missing mandatory files in {self.software_name}:{', '.join(missing_required)}",
            )
            sys.exit(self.log_report.print_log_report(method_name, ["error"]))
        else:
            self.update_all_logs(
                method_name, "valid", "Successfull validation of mandatory files."
            )
        self.log_report.print_log_report(method_name, ["valid", "warning"])
        return

    def add_bioinfo_results_metadata(
        self, files_dict, j_data, sufix, file_tag, output_dir=None
    ):
        """Adds metadata from bioinformatics results to j_data.
        It first calls file_handlers and then maps the handled
        data into j_data.

        Args:
            files_dict (dict{str:str}): A dictionary containing file paths found based on the definitions provided in the bioinformatic JSON file within the software scope (self.software_config).
            j_data (list(dict{str:str}): A list of dictionaries containing metadata lab (list item per sample).
            sufix (str): Sufix added to splitted tables file name.
            output_dir (str): Path to save output files generated during handling_files() process.
            file_tag(str): Tag that will be used for output filenames includes batch date (same as download date) and hex.

        Returns:
            j_data_mapped: A list of dictionaries with bioinformatics metadata mapped into j_data.
        """
        method_name = f"{self.add_bioinfo_results_metadata.__name__}"
        for key in self.software_config.keys():
            # Update bioinfo cofiguration key/scope
            self.current_config_key = key
            map_method_name = f"{method_name}:{self.software_name}.{key}"
            # This skip files that will be parsed with other methods
            if key == "workflow_summary" or key == "fixed_values":
                continue
            try:
                files_dict[key]
                stderr.print(f"[blue]Start processing {self.software_name}.{key}")
                self.log.info(f"Start processing {self.software_name}.{key}")
            except KeyError:
                self.update_all_logs(
                    method_name,
                    "warning",
                    f"No file path found for '{self.software_name}.{key}'",
                )
                continue
            # Handling files
            if self.software_config[key].get("map", True) is False:
                msg = f"File '{self.software_name}.{key}' was processed but skipped from mapping as defined in config."
                self.update_all_logs(map_method_name, "warning", msg)
                self.log_report.print_log_report(map_method_name, ["warning"])
                continue
            data_to_map = self.handling_files(
                files_dict[key], sufix, output_dir, file_tag
            )
            # Mapping data to j_data
            mapping_fields = self.software_config[key].get("content")
            if not mapping_fields:
                self.update_all_logs(
                    map_method_name,
                    "warning",
                    f"No metadata found to perform mapping from '{self.software_name}.{key}' despite 'content' fields being defined.",
                )
                self.log_report.print_log_report(map_method_name, ["warning"])
                continue
            if data_to_map:
                j_data_mapped = self.mapping_over_table(
                    j_data=j_data,
                    map_data=data_to_map,
                    mapping_fields=self.software_config[key]["content"],
                    table_name=files_dict[key],
                )
            else:
                self.update_all_logs(
                    method_name,
                    "warning",
                    f"No metadata found to perform standard mapping when processing '{self.software_name}.{key}'",
                )
                continue
        self.log_report.print_log_report(method_name, ["valid", "warning"])
        return j_data_mapped

    def handling_tables(self, file_list, conf_tab_name):
        """Reads a tabular file in different formats and returns a dictionary containing
        the corresponding data for each sample.

        Args:
            file_list (list): A list of file path/s to be processed.
            conf_tab_name (str): Name of the table in the defined config file.

        Returns:
            data (dict): A dictionary containing metadata as defined in handling_files.
        """
        method_name = f"{self.add_bioinfo_results_metadata.__name__}:{self.handling_tables.__name__}"
        file_ext = os.path.splitext(conf_tab_name)[1]
        sample_idx_colpos = self.get_sample_idx_colpos(self.current_config_key)
        extdict = {".csv": ",", ".tsv": "\t", ".tab": "\t"}
        mapping_fields = self.software_config[self.current_config_key].get("content")

        if not mapping_fields:
            return {}

        if conf_tab_name.endswith(".gz"):
            inner_ext = os.path.splitext(conf_tab_name.strip(".gz"))[1]
            if inner_ext in extdict:
                self.update_all_logs(
                    method_name,
                    "warning",
                    f"Expected tabular file '{conf_tab_name}' is compressed and cannot be processed.",
                )
            return {}

        if file_ext in extdict:
            try:
                return relecov_tools.utils.read_csv_file_return_dict(
                    file_name=file_list[0],
                    sep=extdict[file_ext],
                    key_position=sample_idx_colpos,
                )
            except FileNotFoundError as e:
                self.update_all_logs(
                    method_name,
                    "error",
                    f"Tabular file not found: '{file_list[0]}': {e}",
                )
                raise FileNotFoundError(
                    f"Tabular file not found: '{file_list[0]}'"
                ) from e
        else:
            self.update_all_logs(
                method_name,
                "error",
                f"Unrecognized defined file name extension '{file_ext}' in '{conf_tab_name}'.",
            )
            raise ValueError(self.log_report.print_log_report(method_name, ["error"]))

    def handling_files(self, file_list, sufix, output_dir, file_tag):
        """Handles different file formats to extract data regardless of their structure.
        The goal is to extract the data contained in files specified in ${file_list},
        using either 'standard' handlers defined in this class or pipeline-specific file handlers.
        (inspired from ./metadata_homogenizer.py)

            A file handler method must generate a data structure as follow:
            {
                'SAMPLE1': {
                    'field1': 'value1'
                    'field2': 'value2'
                    'field3': 'value3'
                },
                SAMPLE2': {
                    'field1': 'value1'
                    'field2': 'value2'
                    'field3': 'value3'
                },
                ...
            }
            Note: ensure that 'field1','field2','field3' corresponds with the values
            especified in the 'content' section of each software configuration scope
            (see: conf/bioinfo_config.json).

        Args:
            file_list (list): A list of file path/s to be processed.
            output_dir (str): Path to save output files from imported method if necessary
            file_tag(str): Tag that will be used for output filenames includes batch date (same as download date) and hex.

        Returns:
            data: A dictionary containing bioinfo metadata handled for each sample.
        """
        method_name = f"{self.add_bioinfo_results_metadata.__name__}:{self.handling_files.__name__}"
        splitted_path = os.path.join(output_dir, "analysis_results")
        file_name = self.software_config[self.current_config_key].get("fn")
        # Parsing files
        current_config = self.software_config[self.current_config_key]
        func_name = current_config.get("function")
        if func_name is None:
            data = self.handling_tables(file_list=file_list, conf_tab_name=file_name)
        else:
            if current_config.get("split_by_batch") is True:
                file_extension = current_config.get("fn").rsplit(".", 1)[1]
                base_filename = current_config.get("fn").rsplit(".", 1)[0]
                pattern = re.compile(
                    f"{base_filename}_{sufix}.{re.escape(file_extension)}"
                )
                matching_files = [
                    f for f in os.listdir(splitted_path) if pattern.match(f)
                ]
                full_paths = [  # noqa: F841
                    os.path.join(splitted_path, f) for f in matching_files
                ]
                try:
                    if func_name.startswith("utils/"):
                        utils_name = "relecov_tools.assets.pipeline_utils.utils"
                        func_name = func_name.split("/", 1)[1]
                    else:
                        utils_name = (
                            f"relecov_tools.assets.pipeline_utils.{self.software_name}"
                        )

                    # Dynamically import the function from the specified module
                    import_statement = f"import {utils_name}"
                    exec(import_statement)
                    # Get method name and execute it.
                    data = eval(
                        utils_name
                        + "."
                        + func_name
                        + "(full_paths, file_tag, '"
                        + self.software_name
                        + "', output_dir)"
                    )

                except Exception as e:
                    self.update_all_logs(
                        self.save_merged_files.__name__,
                        "error",
                        f"Error occurred while parsing '{func_name}': {e}.",
                    )
                    sys.exit(self.log_report.print_log_report(method_name, ["error"]))
            else:
                try:
                    # Dynamically import the function from the specified module
                    if func_name.startswith("utils/"):
                        utils_name = "relecov_tools.assets.pipeline_utils.utils"
                        func_name = func_name.split("/", 1)[1]
                    else:
                        utils_name = (
                            f"relecov_tools.assets.pipeline_utils.{self.software_name}"
                        )

                    # Dynamically import the function from the specified module
                    import_statement = f"import {utils_name}"
                    exec(import_statement)
                    # Get method name and execute it.
                    data = eval(
                        utils_name
                        + "."
                        + func_name
                        + "(file_list, file_tag, '"
                        + self.software_name
                        + "', output_dir)"
                    )
                except Exception as e:
                    self.update_all_logs(
                        self.add_bioinfo_results_metadata.__name__,
                        "error",
                        f"Error occurred while parsing '{func_name}': {e}.",
                    )
                    sys.exit(self.log_report.print_log_report(method_name, ["error"]))
        return data

    def add_fixed_values(self, j_data):
        """Add fixed values to j_data as defined in the bioinformatics configuration (definition: "fixed values")

        Args:
            j_data (list(dict{str:str}): A list of dictionaries containing metadata lab (one item per sample).

        Returns:
            j_data: updated j_data with fixxed values added in it.
        """
        method_name = f"{self.add_fixed_values.__name__}"
        try:
            f_values = self.software_config["fixed_values"]
            for row in j_data:
                row["bioinfo_metadata_file"] = self.out_filename
                for field, value in f_values.items():
                    row[field] = value
            self.update_all_logs(method_name, "valid", "Fields added successfully.")
        except KeyError as e:
            self.update_all_logs(
                method_name, "warning", f"Error found while adding fixed values: {e}"
            )
            pass
        self.log_report.print_log_report(method_name, ["valid", "warning"])
        return j_data

    def replace_na_value_if_needed(self, field, raw_val):
        """
        Replace 'NA', None or NaN with 'Not Provided [SNOMED:434941000124101]'
        if the field is not required in the schema.
        """
        required_fields = self.bioinfo_schema.get("required", [])
        is_na = (
            raw_val is None
            or (isinstance(raw_val, str) and raw_val.strip().upper() in ["NA", "NONE"])
            or (isinstance(raw_val, float) and np.isnan(raw_val))
        )
        if is_na and field not in required_fields:
            return "Not Provided [SNOMED:434941000124101]"
        return raw_val

    def add_bioinfo_files_path(self, files_found_dict, j_data):
        """Adds file paths essential for handling and mapping bioinformatics metadata to the j_data.
        For each sample in j_data, the function assigns the corresponding file path based on the identified files in files_found_dict.

        If multiple files are identified per configuration item (e.g., viralrecon.mapping_consensus → *.consensus.fa), each sample in j_data receives its respective file path.
        If no file path is located, the function appends "Not Provided [SNOMED:434941000124101]" to indicate missing data.

        Args:
            files_found_dict (dict): A dictionary containing file paths identified for each configuration item.
            j_data (list(dict{str:str}): A list of dictionaries containing metadata lab (one item per sample).

        Returns:
            j_data: Updated j_data with file paths mapped for bioinformatic metadata.
        """
        method_name = f"{self.add_bioinfo_files_path.__name__}"
        sample_name_error = 0
        multiple_sample_files = self.get_multiple_sample_files()
        for row in j_data:
            if not row.get("sequencing_sample_id"):
                self.update_all_logs(
                    method_name,
                    "warning",
                    f'Sequencing_sample_id missing in {row.get("collecting_sample_id")}... Skipping...',
                )
                continue
            sample_name = row["sequencing_sample_id"]
            base_cod_path = row.get("sequence_file_path_R1")
            if base_cod_path is None:
                self.update_all_logs(
                    method_name,
                    "error",
                    f"No 'sequence_file_path_R1' found for sample {sample_name}. Unable to generate paths.",
                )
                continue
            for key, values in files_found_dict.items():
                file_path = []
                if values:  # Check if value is not empty
                    for file in values:
                        if key in multiple_sample_files:
                            file_path.append(file)
                        elif sample_name in file:
                            file_path.append(file)
                else:
                    file_path.append("Not Provided [SNOMED:434941000124101]")
                if self.software_config[key].get("filepath_name"):
                    path_key = self.software_config[key].get("filepath_name")
                    analysis_results_paths = []
                    for paths in file_path:
                        if file_path != "Not Provided [SNOMED:434941000124101]" and (
                            self.software_config[key].get("extract")
                            or self.software_config[key].get("function")
                        ):
                            analysis_results_path = os.path.join(
                                base_cod_path,
                                "analysis_results",
                                os.path.basename(paths),
                            )
                            analysis_results_paths.append(analysis_results_path)
                        else:
                            analysis_results_paths = file_path
                    row[path_key] = ", ".join(analysis_results_paths)
                else:
                    path_key = key
                if self.software_config[key].get("extract"):
                    self.extract_file(
                        file=file_path,
                        dest_folder=row.get("sequence_file_path_R1"),
                        sample_name=sample_name,
                        path_key=path_key,
                    )
        self.log_report.print_log_report(method_name, ["warning"])
        if sample_name_error == 0:
            self.update_all_logs(method_name, "valid", "File paths added successfully.")
        self.log_report.print_log_report(method_name, ["valid", "warning"])
        return j_data

    def collect_info_from_lab_json(self):
        """Reads lab metadata from a JSON file and creates a list of dictionaries.
        Reads lab metadata from the specified JSON file and converts it into a list of dictionaries.
        This list is used to add the rest of the fields.

        Returns:
            json_lab_data: A list of dictionaries containing lab metadata (aka j_data).
        """
        method_name = f"{self.collect_info_from_lab_json.__name__}"
        try:
            json_lab_data = relecov_tools.utils.read_json_file(
                self.readlabmeta_json_file
            )
        except ValueError:
            self.update_all_logs(
                self.collect_info_from_lab_json.__name__,
                "error",
                f"Invalid lab-metadata json file: self.{self.readlabmeta_json_file}",
            )
            sys.exit(self.log_report.print_log_report(method_name, ["error"]))
        return json_lab_data

    def get_sample_idx_colpos(self, config_key):
        """Extract which column contain sample names for that specific file from config

        Returns:
            sample_idx_possition: column number which contains sample names
        """
        try:
            sample_idx_colpos = self.software_config[config_key]["sample_col_idx"] - 1
        except (KeyError, TypeError):
            sample_idx_colpos = 0
            self.update_all_logs(
                "get_sample_idx_colpos",
                "warning",
                f"No valid sample-index-column defined in '{self.software_name}.{config_key}'. Using default instead.",
            )
        return sample_idx_colpos

    def extract_file(self, file, dest_folder, sample_name=None, path_key=None):
        """Copy input file to the given destination, include sample name and key in log
        Args:
            file (str): Path the file that is going to be copied
            dest_folder (str): Folder with files from batch of samples
            sample_name (str, optional): Name of the sample in metadata. Defaults to None.
            path_key (str, optional): Metadata field for the file. Defaults to None.
        Returns:
            bool: True if the process was successful, else False
        """
        dest_folder = os.path.join(dest_folder, "analysis_results")
        os.makedirs(dest_folder, exist_ok=True)
        for filepath in file:
            out_filepath = os.path.join(dest_folder, os.path.basename(filepath))
            if os.path.isfile(out_filepath):
                self.log.debug(f"{out_filepath} already exists, not extracted")
                continue
            if filepath == "Not Provided [SNOMED:434941000124101]":
                self.update_all_logs(
                    self.extract_file.__name__,
                    "warning",
                    f"File for {path_key} not provided in sample {sample_name}",
                )
                continue
            try:
                shutil.copy(filepath, out_filepath)
            except (IOError, PermissionError) as e:
                self.update_all_logs(
                    self.extract_file.__name__,
                    "warning",
                    f"Could not extract {filepath}: {e}",
                )
                continue
        return True

    def split_data_by_batch(self, j_data):
        """Split metadata from json for each batch of samples found according to folder location of the samples.
        Args:
            files_found_dict (dict): A dictionary containing file paths identified for each configuration item.
            j_data (list(dict)): List of dictionaries, one per sample, including metadata for that sample
        Returns:
            data_by_batch (dict(list(dict))): Dictionary containing parts of j_data corresponding to each
            different folder with samples (batch) included in the original json metadata used as input
        """
        unique_batchs = set([x.get("sequence_file_path_R1") for x in j_data])
        data_by_batch = {batch_dir: {} for batch_dir in unique_batchs}
        for batch_dir in data_by_batch.keys():
            data_by_batch[batch_dir]["j_data"] = [
                samp
                for samp in j_data
                if samp.get("sequence_file_path_R1") == batch_dir
            ]
        return data_by_batch

    def split_tables_by_batch(self, files_found_dict, sufix, batch_data, output_dir):
        """Filter table content to output a new table containing only the samples present in given metadata
        Args:
            files_found_dict (dict): A dictionary containing file paths identified for each configuration item.
            sufix (str): Sufix to be added to the new table file name.
            batch_data (list(dict)): Metadata corresponding to a single folder with samples (folder)
            output_dir (str): Output location for the generated tabular file
        """

        def extract_batch_rows_to_file(file, sufix):
            """Create a new table file only with rows matching samples in batch_data"""
            extdict = {".csv": ",", ".tsv": "\t", ".tab": "\t"}
            file_extension = os.path.splitext(file)[1]
            file_df = pd.read_csv(
                file, sep=extdict.get(file_extension), header=header_pos
            )
            sample_col = file_df.columns[sample_colpos]
            file_df[sample_col] = file_df[sample_col].astype(str)
            file_df = file_df[file_df[sample_col].isin(batch_samples)]

            base, ext = os.path.splitext(os.path.basename(file))
            new_filename = f"{base}_{sufix}{ext}"
            os.makedirs(os.path.join(output_dir, "analysis_results"), exist_ok=True)
            output_path = os.path.join(output_dir, "analysis_results", new_filename)
            file_df.to_csv(output_path, index=False, sep=extdict.get(file_extension))
            return

        method_name = self.split_tables_by_batch.__name__
        namekey = "sequencing_sample_id"
        batch_samples = [row.get(namekey) for row in batch_data]
        for key, files in files_found_dict.items():
            if not self.software_config[key].get("split_by_batch"):
                continue
            header_pos = self.software_config[key].get("header_row_idx", 1) - 1
            sample_colpos = self.get_sample_idx_colpos(key)
            for file in files:
                try:
                    extract_batch_rows_to_file(file, sufix)
                except Exception as e:
                    if self.software_config[key].get("required"):
                        log_type = "error"
                    else:
                        log_type = "warning"
                    self.update_all_logs(
                        method_name,
                        log_type,
                        f"Could not create batch table for {file}: {e}",
                    )
        self.log_report.print_log_report(method_name, ["valid", "warning", "error"])
        return

    def merge_metadata(self, batch_filepath, batch_data):
        """
        Merge metadata json if sample does not exist in the metadata file,
        or prompt the user to update if --update flag is provided and sample differs.

        Args:
            batch_filepath (str): Path to save the json file with the metadata.
            batch_data (list): A list of dictionaries containing metadata of the samples.

        Returns:
            merged_metadata (list): The updated list of metadata entries.
        """
        merged_metadata = relecov_tools.utils.read_json_file(batch_filepath)
        prev_metadata_dict = {
            item["sequencing_sample_id"]: item for item in merged_metadata
        }

        overwrite_all = False

        for item in batch_data:
            sample_id = item["sequencing_sample_id"]
            if sample_id in prev_metadata_dict:
                if prev_metadata_dict[sample_id] != item:
                    if self.update:
                        if not overwrite_all:
                            stderr.print(
                                f"[red]Sample '{sample_id}' has different metadata than the existing entry in {batch_filepath}."
                            )
                            response = Prompt.ask(
                                "[yellow]Do you want to overwrite this sample?",
                                choices=["y", "n", "all"],
                                default="n",
                            )
                            if response == "all":
                                overwrite_all = True
                                stderr.print(
                                    "[green]All subsequent samples will be overwritten automatically."
                                )
                                prev_metadata_dict[sample_id] = item
                            elif response == "y":
                                prev_metadata_dict[sample_id] = item
                                stderr.print(f"[green]Sample '{sample_id}' updated.")
                            else:
                                stderr.print(
                                    f"[blue]Skipping update for sample '{sample_id}'."
                                )
                        else:
                            prev_metadata_dict[sample_id] = item
                            stderr.print(f"[green]Sample '{sample_id}' updated (auto).")
                    else:
                        stderr.print(
                            f"[red]Sample '{sample_id}' has different data in {batch_filepath} and new metadata. Can't merge."
                        )
                        self.log.error(
                            "Sample %s has different data in %s and new metadata. Can't merge.",
                            sample_id,
                            batch_filepath,
                        )
                        sys.exit(1)
            else:
                prev_metadata_dict[sample_id] = item

        merged_metadata = list(prev_metadata_dict.values())
        relecov_tools.utils.write_json_to_file(merged_metadata, batch_filepath)
        return merged_metadata

    def save_merged_files(self, files_dict, file_tag, output_dir=None):
        """
        Process and save files that where split by cod and that have a function to be processed

        Args:
            files_dict (dict): A dictionary containing file paths identified for each configuration item.
            file_tag (str): Tag that will be used for output filenames includes batch date (same as download date) and hex.
            output_dir (str): Path to save output files generated during processing.

        Returns:
            None
        """
        method_name = f"{self.save_merged_files.__name__}"
        for key, config in self.software_config.items():
            func_name = config.get("function")
            # Skip configurations that do not match the conditions
            if config.get("split_by_batch") is True and func_name:
                try:
                    file_path = files_dict[key]
                    stderr.print(f"[blue]Processing splitted file: {file_path}")
                    self.log.info(f"Processing splitted file: {file_path}")
                except KeyError:
                    self.update_all_logs(
                        method_name,
                        "warning",
                        f"No file path found for '{file_path}'",
                    )
                    continue
                try:
                    # Dynamically import the function from the specified module
                    if func_name.startswith("utils/"):
                        utils_name = "relecov_tools.assets.pipeline_utils.utils"
                        func_name = func_name.split("/", 1)[1]
                    else:
                        utils_name = (
                            f"relecov_tools.assets.pipeline_utils.{self.software_name}"
                        )

                    import_statement = f"import {utils_name}"
                    exec(import_statement)
                    # Get method name and execute it.
                    data = eval(
                        utils_name
                        + "."
                        + func_name
                        + "(file_path, file_tag, '"
                        + self.software_name
                        + "', output_dir)"
                    )
                except Exception as e:
                    self.update_all_logs(
                        self.save_merged_files.__name__,
                        "error",
                        f"Error occurred while parsing '{func_name}': {e}.",
                    )
                    sys.exit(self.log_report.print_log_report(method_name, ["error"]))
        return data

    def get_multiple_sample_files(self):
        multiple_sample_files = []
        for key in self.software_config.keys():
            if self.software_config[key].get("multiple_samples"):
                multiple_sample_files.append(key)
        return multiple_sample_files

    def filter_properties(self, data):
        """
        Remove properties from bioinfo_metadata that are not in the schema properties.

        Parameters
        ----------
        data : list[dict]
            Dictionary with the bioinfo metadata

        Returns
        -------
        data: list[dict]
            Bioinfo metadata json filtered
        """
        valid_keys = set(self.json_schema.get("properties", {}).keys())

        for sample in data:
            for k in list(sample.keys()):
                if k not in valid_keys:
                    sample.pop(k)
        return data

    def create_bioinfo_file(self):
        """Create the bioinfodata json with collecting information from lab
        metadata json, mapping_stats, and more information from the files
        inside input directory.

        Returns:
            bool: True if the bioinfo file creation process was successful.
        """

        # Check samplesheet for matching samples
        self.validate_samplenames()

        # Find and validate bioinfo files
        stderr.print("[blue]Scanning input directory...")
        files_found_dict = self.scann_directory()
        stderr.print("[blue]Validating required files...")
        self.validate_software_mandatory_files(files_found_dict)
        # Split files found based on each batch of samples
        data_by_batch = self.split_data_by_batch(self.j_data)
        sufix = datetime.now().strftime("%Y%m%d%H%M%S")

        # Add bioinfo metadata to j_data
        for batch_dir, batch_dict in data_by_batch.items():
            batch_data = batch_dict["j_data"]
            if not batch_data:
                self.log.warning(
                    f"Data from batch {batch_dir} was completely empty. Skipped."
                )
                self.update_all_logs(
                    self.create_bioinfo_file.__name__,
                    "warning",
                    f"Data from batch {batch_dir} was completely empty. Skipped.",
                )
                continue
            first_sample = batch_data[0]
            lab_code = first_sample.get(
                "submitting_institution_id", batch_dir.split("/")[-2]
            )
            batch_date = first_sample.get("batch_id", batch_dir.split("/")[-1])
            self.set_batch_id(batch_date)
            self.logsum.feed_key(batch_dir)
            file_tag = batch_date + "_" + self.hex
            stderr.print(f"[blue]Processing data from {batch_dir}")
            stderr.print("[blue]Adding bioinfo metadata to read lab metadata...")
            self.split_tables_by_batch(files_found_dict, sufix, batch_data, batch_dir)
            batch_data = self.add_bioinfo_results_metadata(
                files_found_dict, batch_data, sufix, file_tag, batch_dir
            )
            stderr.print("[blue]Adding software versions to read lab metadata...")
            stderr.print("[blue]Adding fixed values")
            batch_data = self.add_fixed_values(batch_data)
            # Adding files path
            stderr.print("[blue]Adding files path to read lab metadata")
            batch_data = self.add_bioinfo_files_path(files_found_dict, batch_data)
            tag = "bioinfo_lab_metadata_"
            batch_filename = tag + lab_code + ".json"
            batch_filename = self.tag_filename(batch_filename)
            batch_filepath = os.path.join(batch_dir, batch_filename)
            module = eval(f"relecov_tools.assets.pipeline_utils.{self.software_name}")
            try:
                if hasattr(module, "quality_control_evaluation"):
                    qc_func = eval(
                        f"relecov_tools.assets.pipeline_utils.{self.software_name}.quality_control_evaluation"
                    )
                    qc_data = qc_func(batch_data)
                    for sample in batch_data:
                        sample_id = sample.get("sequencing_sample_id")
                        if sample_id in qc_data:
                            sample.update(qc_data[sample_id])
            except (AttributeError, NameError, TypeError, ValueError) as e:
                self.update_all_logs(
                    self.create_bioinfo_file.__name__,
                    "warning",
                    f"Could not evaluate quality_control_evaluation for batch {batch_dir}: {e}",
                )
                stderr.print(
                    f"[orange]Could not evaluate quality_control_evaluation for batch {batch_dir}: {e}"
                )

            # Filter properties from batch_data that are not included in the schema
            batch_data = self.filter_properties(batch_data)

            if os.path.exists(batch_filepath):
                stderr.print(
                    f"[blue]Bioinfo metadata {batch_filepath} file already exists. Merging new data if possible."
                )
                self.log.info(
                    "Bioinfo metadata %s file already exists. Merging new data if possible."
                    % batch_filepath
                )
                batch_data = self.merge_metadata(batch_filepath, batch_data)
            else:
                relecov_tools.utils.write_json_to_file(batch_data, batch_filepath)
            for sample in batch_data:
                self.logsum.feed_key(
                    key=batch_dir, sample=sample.get("sequencing_sample_id")
                )
            self.log.info("Created output json file: %s" % batch_filepath)
            stderr.print(f"[green]Created batch json file: {batch_filepath}")

        year = str(datetime.now().year)
        out_path = os.path.join(self.output_dir, year)
        os.makedirs(out_path, exist_ok=True)

        stderr.print("[blue]Saving previously splitted files to output directory")
        batch_dates = []
        # Get batch date for all the samples
        for batch_dir, batch_dict in data_by_batch.items():
            if batch_dir.split("/")[-1] not in batch_dates:
                batch_dates.append(batch_dir.split("/")[-1])

        if len(batch_dates) == 1:
            batch_date = str(batch_dates[0])
        else:
            stderr.print(
                "[orange]More than one batch date in the same json data. Using current date as batch date."
            )
            self.log.info(
                "More than one batch date in the same json data. Using current date as batch date."
            )
            batch_date = self.basemod_date
        self.set_batch_id(batch_date)
        file_tag = self.batch_id + "_" + self.hex
        self.save_merged_files(files_found_dict, file_tag, out_path)
        batch_filename = self.tag_filename("bioinfo_lab_metadata" + ".json")
        stderr.print("[blue]Writting output json file")
        file_path = os.path.join(out_path, batch_filename)
        if os.path.exists(file_path):
            stderr.print(
                f"[blue]Bioinfo metadata {file_path} file already exists. Merging new data if possible."
            )
            self.log.info(
                "Bioinfo metadata %s file already exists. Merging new data if possible."
                % file_path
            )
            batch_data = self.merge_metadata(file_path, self.j_data)
        else:
            relecov_tools.utils.write_json_to_file(self.j_data, file_path)
        stderr.print(f"[green]Sucessful creation of bioinfo analyis file: {file_path}")
        self.parent_create_error_summary(
            called_module="read-bioinfo-metadata", logs=self.logsum.logs
        )
        return True
