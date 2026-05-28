module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n12,
    n11,
    n6,
    n7,
    n8,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output [3:0] n12;
  output n11;
  output n6;
  output n7;
  output n8;
  output n9;
  output n10;

  wire \$and$testcase/test87/test87.v:24$10_Y , \$and$testcase/test87/test87.v:33$20_Y , \$and$testcase/test87/test87.v:37$25_Y , \$or$testcase/test87/test87.v:19$3_Y , \$or$testcase/test87/test87.v:25$12_Y , \$xor$testcase/test87/test87.v:20$5_Y , \$xor$testcase/test87/test87.v:49$33_Y , ___xor_testcase_test87_test87_v_20_5_n1_0__, ___xor_testcase_test87_test87_v_20_5_n2_0__, ___xor_testcase_test87_test87_v_20_5_n3_0__, ___xor_testcase_test87_test87_v_21_7_n1_0__, ___xor_testcase_test87_test87_v_21_7_n2_0__, ___xor_testcase_test87_test87_v_21_7_n3_0__, ___xor_testcase_test87_test87_v_28_15_n1_0__, ___xor_testcase_test87_test87_v_28_15_n2_0__, ___xor_testcase_test87_test87_v_28_15_n3_0__, ___xor_testcase_test87_test87_v_32_19_n1_0__, ___xor_testcase_test87_test87_v_32_19_n2_0__, ___xor_testcase_test87_test87_v_32_19_n3_0__, ___xor_testcase_test87_test87_v_36_24_n1_0__, ___xor_testcase_test87_test87_v_36_24_n2_0__, ___xor_testcase_test87_test87_v_36_24_n3_0__, ___xor_testcase_test87_test87_v_49_33_n1_0__, ___xor_testcase_test87_test87_v_49_33_n2_0__, ___xor_testcase_test87_test87_v_49_33_n3_0__, n13, n14, n15, n16, n17, n18, n20, n21, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n56, n57, n63, n80, n81, n82;

  and   \$and$testcase/test87/test87.v:54$35  (n80, n2[0], n2[1]);
  and   \$and$testcase/test87/test87.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test87/test87.v:27$14  (n30, n2[1], n3[1]);
  and   \$and$testcase/test87/test87.v:30$17  (n40, n2[2], n3[2]);
  and   \$and$testcase/test87/test87.v:34$22  (n44, n2[2], n3[2]);
  nand  xor_nand_9 (___xor_testcase_test87_test87_v_32_19_n1_0__, n2[2], n3[2]);
  nand  xor_nand_12 (___xor_testcase_test87_test87_v_36_24_n1_0__, n2[2], n3[2]);
  not   \$not$testcase/test87/test87.v:46$39  (n63, n4);
  and   \$and$testcase/test87/test87.v:33$20  (\$and$testcase/test87/test87.v:33$20_Y , n2[2], n5);
  and   \$and$testcase/test87/test87.v:37$25  (\$and$testcase/test87/test87.v:37$25_Y , n2[2], n5);
  or    \$or$testcase/test87/test87.v:31$18  (n41, n2[2], n5);
  or    \$or$testcase/test87/test87.v:35$23  (n45, n2[2], n5);
  or    \$or$testcase/test87/test87.v:55$36  (n81, n80, n3[0]);
  or    \$or$testcase/test87/test87.v:17$2  (n14, n13, n4);
  nand  xor_nand_6 (___xor_testcase_test87_test87_v_28_15_n1_0__, n30, n5);
  nand  xor_nand_10 (___xor_testcase_test87_test87_v_32_19_n2_0__, n2[2], ___xor_testcase_test87_test87_v_32_19_n1_0__);
  nand  xor_nand_11 (___xor_testcase_test87_test87_v_32_19_n3_0__, n3[2], ___xor_testcase_test87_test87_v_32_19_n1_0__);
  nand  xor_nand_13 (___xor_testcase_test87_test87_v_36_24_n2_0__, n2[2], ___xor_testcase_test87_test87_v_36_24_n1_0__);
  nand  xor_nand_14 (___xor_testcase_test87_test87_v_36_24_n3_0__, n3[2], ___xor_testcase_test87_test87_v_36_24_n1_0__);
  not   \$not$testcase/test87/test87.v:33$21  (n43, \$and$testcase/test87/test87.v:33$20_Y );
  not   \$not$testcase/test87/test87.v:37$26  (n47, \$and$testcase/test87/test87.v:37$25_Y );
  or    \$or$testcase/test87/test87.v:38$27  (n56, n40, n41);
  not   \$not$testcase/test87/test87.v:56$41  (n82, n81);
  not   \$not$testcase/test87/test87.v:18$37  (n15, n14);
  nand  xor_nand_7 (___xor_testcase_test87_test87_v_28_15_n2_0__, n30, ___xor_testcase_test87_test87_v_28_15_n1_0__);
  nand  xor_nand_8 (___xor_testcase_test87_test87_v_28_15_n3_0__, n5, ___xor_testcase_test87_test87_v_28_15_n1_0__);
  nand  \$xor$testcase/test87/test87.v:32$19  (n42, ___xor_testcase_test87_test87_v_32_19_n2_0__, ___xor_testcase_test87_test87_v_32_19_n3_0__);
  nand  \$xor$testcase/test87/test87.v:36$24  (n46, ___xor_testcase_test87_test87_v_36_24_n2_0__, ___xor_testcase_test87_test87_v_36_24_n3_0__);
  or    \$or$testcase/test87/test87.v:19$3  (\$or$testcase/test87/test87.v:19$3_Y , n15, n5);
  nand  \$xor$testcase/test87/test87.v:28$15  (n31, ___xor_testcase_test87_test87_v_28_15_n2_0__, ___xor_testcase_test87_test87_v_28_15_n3_0__);
  or    \$or$testcase/test87/test87.v:39$28  (n57, n56, n42);
  not   \$not$testcase/test87/test87.v:19$4  (n16, \$or$testcase/test87/test87.v:19$3_Y );
  or    \$or$testcase/test87/test87.v:29$16  (n7, n31, n4);
  or    \$or$testcase/test87/test87.v:40$29  (n8, n57, n43);
  nand  xor_nand_0 (___xor_testcase_test87_test87_v_20_5_n1_0__, n16, n2[1]);
  nand  xor_nand_15 (___xor_testcase_test87_test87_v_49_33_n1_0__, n31, n8);
  nand  xor_nand_1 (___xor_testcase_test87_test87_v_20_5_n2_0__, n16, ___xor_testcase_test87_test87_v_20_5_n1_0__);
  nand  xor_nand_2 (___xor_testcase_test87_test87_v_20_5_n3_0__, n2[1], ___xor_testcase_test87_test87_v_20_5_n1_0__);
  nand  xor_nand_16 (___xor_testcase_test87_test87_v_49_33_n2_0__, n31, ___xor_testcase_test87_test87_v_49_33_n1_0__);
  nand  xor_nand_17 (___xor_testcase_test87_test87_v_49_33_n3_0__, n8, ___xor_testcase_test87_test87_v_49_33_n1_0__);
  nand  \$xor$testcase/test87/test87.v:20$5  (\$xor$testcase/test87/test87.v:20$5_Y , ___xor_testcase_test87_test87_v_20_5_n2_0__, ___xor_testcase_test87_test87_v_20_5_n3_0__);
  nand  \$xor$testcase/test87/test87.v:49$33  (\$xor$testcase/test87/test87.v:49$33_Y , ___xor_testcase_test87_test87_v_49_33_n2_0__, ___xor_testcase_test87_test87_v_49_33_n3_0__);
  not   \$not$testcase/test87/test87.v:20$6  (n17, \$xor$testcase/test87/test87.v:20$5_Y );
  not   \$not$testcase/test87/test87.v:49$34  (n11, \$xor$testcase/test87/test87.v:49$33_Y );
  nand  xor_nand_3 (___xor_testcase_test87_test87_v_21_7_n1_0__, n17, n3[1]);
  nand  xor_nand_4 (___xor_testcase_test87_test87_v_21_7_n2_0__, n17, ___xor_testcase_test87_test87_v_21_7_n1_0__);
  nand  xor_nand_5 (___xor_testcase_test87_test87_v_21_7_n3_0__, n3[1], ___xor_testcase_test87_test87_v_21_7_n1_0__);
  nand  \$xor$testcase/test87/test87.v:21$7  (n18, ___xor_testcase_test87_test87_v_21_7_n2_0__, ___xor_testcase_test87_test87_v_21_7_n3_0__);
  or    \$or$testcase/test87/test87.v:23$9  (n20, n18, n2[2]);
  and   \$and$testcase/test87/test87.v:24$10  (\$and$testcase/test87/test87.v:24$10_Y , n20, n3[2]);
  not   \$not$testcase/test87/test87.v:24$11  (n21, \$and$testcase/test87/test87.v:24$10_Y );
  or    \$or$testcase/test87/test87.v:25$12  (\$or$testcase/test87/test87.v:25$12_Y , n21, n5);
  not   \$not$testcase/test87/test87.v:25$13  (n6, \$or$testcase/test87/test87.v:25$12_Y );

  assign n12[0] = n6;
  assign n12[1] = n7;
  assign n12[2] = n8;
  assign n9 = 1'b1;
  assign n10 = n4;
  assign n12[3] = 1'b1;

endmodule
